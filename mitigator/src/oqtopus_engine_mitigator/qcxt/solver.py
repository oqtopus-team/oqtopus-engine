from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

import networkx as nx  # type: ignore[import-untyped]
import numpy as np
from scipy.optimize import (  # type: ignore[import-untyped]
    Bounds,
    LinearConstraint,
    OptimizeResult,
    milp,
)
from scipy.sparse import coo_matrix  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from qiskit import QuantumCircuit  # type: ignore[import-untyped]

    from oqtopus_engine_mitigator.qcxt.device import DeviceData

logger = logging.getLogger(__name__)

_MIN_ERROR_RATE = 1e-9
_BINARY_THRESHOLD = 0.5

# Define type aliases for NetworkX graph nodes and edges with fidelity attributes.
type Node = tuple[int, dict[str, Any]]  # (node_id, attributes)
type Edge = tuple[int, int, int, dict[str, Any]]  # (control, target, key, attributes)


@dataclass(frozen=True)
class SelectedCut:
    """Class to represent a selected CX gate to be cut in the quantum circuit.

    Attributes:
        control: The control qubit index of the CX gate.
        target: The target qubit index of the CX gate.
        edge_key: The unique key identifying the edge in the graph.
        fidelity: The fidelity of the CX gate.
        instruction_index: The index of the instruction in the original circuit.

    """

    control: int
    target: int
    edge_key: int
    fidelity: float
    instruction_index: int

    @classmethod
    def from_edge(cls, edge_index: int, edge: Edge) -> Self:
        """Create a SelectedCut instance from a graph edge.

        Args:
            edge_index: The index of the edge in the graph.
            edge: A tuple representing the edge in the graph.

        Returns:
            A SelectedCut instance representing the edge.

        """
        control, target, _key, attrs = edge
        return cls(
            control=control,
            target=target,
            edge_key=edge_index,
            fidelity=attrs["fidelity"],
            instruction_index=attrs["instruction_index"],
        )


@dataclass(frozen=True)
class CutContext:
    """Context for the cut selection problem, encapsulating the graph and parameters.

    Attributes:
        nodes: Tuple of graph nodes with their attributes.
        edges: Tuple of graph edges with their attributes.
        max_cuts: Maximum number of cuts allowed.
        cut_fidelity_threshold: Fidelity threshold below which cuts are forced.
        min_partition_ratio: Minimum ratio of total node fidelity for partitioning.

    """

    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    max_cuts: int
    cut_fidelity_threshold: float
    min_partition_ratio: float

    @property
    def n_nodes(self) -> int:
        """Number of nodes in the graph."""
        return len(self.nodes)

    @property
    def n_edges(self) -> int:
        """Number of edges in the graph."""
        return len(self.edges)

    @property
    def n_vars(self) -> int:
        """Total number of MIP variables."""
        return self.n_nodes + self.n_edges

    def low_fidelity_edges(self) -> list[int]:
        """Edge indices whose fidelity is below the cut_fidelity_threshold.

        Returns:
            A list of edge indices (no fidelity-based ordering).

        """
        return [
            k
            for k, edge in enumerate(self.edges)
            if edge[3]["fidelity"] < self.cut_fidelity_threshold
        ]

    def take_lowest_fidelity_edges(self, edge_indices: list[int]) -> list[int]:
        """Sort edge indices by fidelity and return the lowest ones up to max_cuts.

        Args:
            edge_indices: List of edge indices to consider.

        Returns:
            A list of edge indices sorted by fidelity, limited to max_cuts.

        """
        sorted_edges = sorted(edge_indices, key=lambda k: self.edges[k][3]["fidelity"])
        return sorted_edges[: self.max_cuts]

    def forced_cut_edge_indices(self) -> set[int]:
        """Select indices of edges that must be cut based on the fidelity threshold.

        Edges whose fidelity is below the threshold are forced to be cut,
        limited to the worst max_cuts of them.

        Returns:
            A set of edge indices that are forced to be cut.

        """
        return set(self.take_lowest_fidelity_edges(self.low_fidelity_edges()))


@dataclass
class ConstraintBuilder:
    """Builder for constructing linear constraints in a sparse matrix format.

    Attributes:
        rows: List of row indices for non-zero entries in the constraint matrix.
        cols: List of column indices for non-zero entries in the constraint matrix.
        vals: List of values for non-zero entries in the constraint matrix.
        lower: List of lower bounds for each constraint row.
        upper: List of upper bounds for each constraint row.
        row_count: Total number of constraint rows added.

    """

    rows: list[int] = field(default_factory=list)
    cols: list[int] = field(default_factory=list)
    vals: list[float] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    row_count: int = 0

    def add_constraint(
        self,
        terms: list[tuple[int, float]],
        *,
        lower: float,
        upper: float,
    ) -> None:
        """Append a new linear constraint row to the builder.

        Args:
            terms: A list of (column_index, coefficient) pairs.
            lower: Lower bound for the constraint.
            upper: Upper bound for the constraint.

        """
        for col, val in terms:
            self.rows.append(self.row_count)
            self.cols.append(col)
            self.vals.append(val)
        self.lower.append(lower)
        self.upper.append(upper)
        self.row_count += 1


class CutSelector:
    """Select CX gates to cut in a quantum circuit using a MIP formulation.

    Attributes:
        device: An instance of DeviceData containing fidelity for qubits and gates.
        max_cuts: Maximum number of cuts allowed.
        cut_fidelity_threshold: Fidelity threshold below which cuts are forced.
        min_partition_ratio: Minimum ratio of total node fidelity for partitioning.

    """

    def __init__(
        self,
        device: DeviceData,
        *,
        max_cuts: int,
        cut_fidelity_threshold: float,
        min_partition_ratio: float,
    ) -> None:
        self.device = device
        self.max_cuts = max_cuts
        self.cut_fidelity_threshold = cut_fidelity_threshold
        self.min_partition_ratio = min_partition_ratio

    def circuit_to_graph(self, circuit: QuantumCircuit) -> nx.MultiDiGraph:
        """Convert a Qiskit QuantumCircuit to a directed multigraph.

        The graph nodes represent qubits, and edges represent CX gates.
        Other gates such as single-qubit gates and other multi-qubit gates are ignored
        for the purpose of cut selection.

        Args:
            circuit: QuantumCircuit object.

        Returns:
            A networkx MultiDiGraph representing the circuit.

        """
        graph = nx.MultiDiGraph()

        # Add nodes for each qubit with their corresponding fidelities
        for qid in self.device.one_qubit_fidelities:
            graph.add_node(qid, fidelity=self.device.one_qubit_fidelities[qid])

        # Add edges for each CX gate with their corresponding fidelities
        # Mapping virtual qubits to physical qubits is the same as for nodes.
        edge_key = 0
        for instruction_index, instruction in enumerate(circuit.data):
            if instruction.operation.name != "cx":
                continue
            ctrl_idx = circuit.find_bit(instruction.qubits[0]).index
            tgt_idx = circuit.find_bit(instruction.qubits[1]).index

            graph.add_edge(
                ctrl_idx,
                tgt_idx,
                key=edge_key,
                gate="cx",
                fidelity=self.device.two_qubit_fidelities[ctrl_idx, tgt_idx],
                instruction_index=instruction_index,
            )
            edge_key += 1

        return graph

    def solve(self, graph: nx.MultiDiGraph) -> list[SelectedCut]:
        """Select edges to cut by solving the MIP problem.

        Args:
            graph: A networkx MultiDiGraph representing the quantum circuit.

        Returns:
            A list of SelectedCut objects representing the CX gates to cut.

        """
        ctx = CutContext(
            nodes=tuple(graph.nodes(data=True)),
            edges=tuple(graph.edges(keys=True, data=True)),
            max_cuts=self.max_cuts,
            cut_fidelity_threshold=self.cut_fidelity_threshold,
            min_partition_ratio=self.min_partition_ratio,
        )
        logger.debug(
            "The cut selection problem is being solved with the following parameters: "
            "max_cuts=%d, cut_fidelity_threshold=%.4f, min_partition_ratio=%.4f",
            self.max_cuts,
            self.cut_fidelity_threshold,
            self.min_partition_ratio,
        )

        # Worst-fidelity edges are forced to be cut up front
        forced_cuts = ctx.forced_cut_edge_indices()
        logger.debug(
            "Forced cuts (below fidelity threshold): count=%d",
            len(forced_cuts),
        )

        # Build the MIP objective function
        objective, bounds = _build_objective_and_bounds(ctx, forced_cuts)

        # Solve the MIP problem
        result = milp(
            c=objective,
            constraints=_build_constraints(ctx),
            integrality=np.ones(ctx.n_vars),
            bounds=bounds,
        )

        # If the MIP solver fails, fall back to selecting cuts based on edge fidelity
        if not result.success:
            logger.debug(
                "MIP solver failed with status %s."
                "Falling back to lowest-fidelity cut selection up to max_cuts=%d.",
                result.message,
                ctx.max_cuts,
            )
            return _select_lowest_fidelity_cuts(ctx)

        logger.debug("MIP solver succeeded with status %s.", result.message)

        return _collect_cuts_from_solution(result, ctx)


def _build_objective_and_bounds(
    ctx: CutContext, forced_cuts: set[int]
) -> tuple[np.ndarray, Bounds]:
    """Construct the objective function and variable bounds for the MIP problem.

    Args:
        ctx: CutContext containing edge information and fidelity threshold.
        forced_cuts: Set of edge indices that are forced to be cut.

    Returns:
        A tuple containing objective function and variable bounds for MIP problem.

    """
    objective = np.zeros(ctx.n_vars)
    lower = np.zeros(ctx.n_vars)
    upper = np.ones(ctx.n_vars)

    for k, edge in enumerate(ctx.edges):
        cut_var_idx = ctx.n_nodes + k
        if k in forced_cuts:
            lower[cut_var_idx] = 1.0  # Pin z_k = 1 for forced cuts
            continue
        fidelity = edge[3]["fidelity"]
        error_rate = max(1.0 - fidelity, _MIN_ERROR_RATE)
        objective[cut_var_idx] = fidelity / error_rate

    return objective, Bounds(lower, upper)


def _add_cut_indicator_constraints(builder: ConstraintBuilder, ctx: CutContext) -> None:
    """Enforce logical constraints between node assignments and cut variables."""
    node_index_map = {nid: i for i, (nid, _) in enumerate(ctx.nodes)}
    for k, (u, v, _, _) in enumerate(ctx.edges):
        u_idx = node_index_map[u]
        v_idx = node_index_map[v]
        cut_var_idx = ctx.n_nodes + k
        # Enforce z_k >= x_v - x_u and z_k >= x_u - x_v
        builder.add_constraint(
            [(u_idx, -1.0), (v_idx, 1.0), (cut_var_idx, 1.0)], lower=0.0, upper=np.inf
        )
        builder.add_constraint(
            [(u_idx, 1.0), (v_idx, -1.0), (cut_var_idx, 1.0)], lower=0.0, upper=np.inf
        )


def _add_max_cuts_constraint(builder: ConstraintBuilder, ctx: CutContext) -> None:
    """Add a constraint to limit the total number of cuts to max_cuts."""
    terms = [(ctx.n_nodes + k, 1.0) for k in range(ctx.n_edges)]
    builder.add_constraint(terms, lower=0.0, upper=float(ctx.max_cuts))


def _add_balance_constraint(builder: ConstraintBuilder, ctx: CutContext) -> None:
    """Add a constraint to balance the total node fidelity across two partitions."""
    node_fidelities = [data["fidelity"] for _, data in ctx.nodes]
    total = sum(node_fidelities)
    # Node variables occupy the first n_nodes columns, so each column index
    # equals its node position (the enumerate index).
    terms = list(enumerate(node_fidelities))
    builder.add_constraint(
        terms,
        lower=ctx.min_partition_ratio * total,
        upper=(1.0 - ctx.min_partition_ratio) * total,
    )


def _build_constraints(ctx: CutContext) -> LinearConstraint:
    """Construct the linear constraints for the MIP problem based on the context.

    Args:
        ctx: CutContext containing nodes, edges, and constraint parameters.

    Returns:
        A LinearConstraint object representing the constraints of the MIP problem.

    """
    builder = ConstraintBuilder()
    _add_cut_indicator_constraints(builder, ctx)
    _add_max_cuts_constraint(builder, ctx)
    _add_balance_constraint(builder, ctx)

    matrix = coo_matrix(
        (builder.vals, (builder.rows, builder.cols)),
        shape=(builder.row_count, ctx.n_vars),
    )
    return LinearConstraint(matrix, builder.lower, builder.upper)


def _collect_cuts_from_solution(
    result: OptimizeResult, ctx: CutContext
) -> list[SelectedCut]:
    """Extract the selected cuts from the MIP solution.

    Args:
        result: The optimization result object returned by the MIP solver.
        ctx: CutContext containing edge information.

    Returns:
        A list of SelectedCut objects representing the selected cuts.

    """
    # z_k: cut indicator variables (the latter n_edges variables in the solution)
    z = np.asarray(result.x[ctx.n_nodes :])
    cut_edges = np.flatnonzero(z > _BINARY_THRESHOLD).tolist()

    # Keep only the worst-fidelity edges if the number of cuts exceeds max_cuts
    if len(cut_edges) > ctx.max_cuts:
        logger.debug(
            "Number of selected cuts (%d) exceeds max_cuts (%d). "
            "Selecting the worst-fidelity edges.",
            len(cut_edges),
            ctx.max_cuts,
        )
        cut_edges = ctx.take_lowest_fidelity_edges(cut_edges)

    return [SelectedCut.from_edge(k, ctx.edges[k]) for k in cut_edges]


def _select_lowest_fidelity_cuts(ctx: CutContext) -> list[SelectedCut]:
    """Select the lowest-fidelity edges to cut, up to max_cuts.

    This simply selects the edges with the lowest fidelity values, ignoring all
    MIP constraints, as a fallback selection strategy.

    Args:
        ctx: CutContext containing edge information.

    Returns:
        A list of SelectedCut objects representing the selected cuts.

    """
    return [
        SelectedCut.from_edge(k, ctx.edges[k])
        for k in ctx.take_lowest_fidelity_edges(list(range(ctx.n_edges)))
    ]
