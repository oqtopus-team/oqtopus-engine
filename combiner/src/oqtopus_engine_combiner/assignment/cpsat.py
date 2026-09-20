from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx  # type: ignore[import-untyped]
from ortools.sat.python import cp_model

from oqtopus_engine_combiner.assignment.base import (
    AssignmentMatch,
    AssignmentStrategyBase,
)

if TYPE_CHECKING:
    from oqtopus_engine_combiner.mp_auto import JobWithCircuitGraph

logger = logging.getLogger(__name__)


class CpsatAssignmentStrategy(AssignmentStrategyBase):
    """CP-SAT baseline for exact directed subgraph embedding."""

    name = "cpsat"

    def __init__(self, *, verify: bool = False) -> None:
        # Keep the baseline solving logic unchanged; verification only checks results.
        self._verify = verify

    def assign(
        self,
        t: nx.Graph,
        jobs: list[JobWithCircuitGraph],
        inferred_topology: nx.Graph | None = None,
        *,
        idle_qubits_insertion_enabled: bool = False,
    ) -> list[AssignmentMatch]:
        """Find subgraphs in jobs' circuit graphs that can be mapped to T.

        Args:
            t: Target graph T representing the device connectivity.
            jobs: List of job dictionaries, each containing a 'circuit_graph' key
                with the circuit's graph.
            inferred_topology: This graph representing the device connectivity.
            idle_qubits_insertion_enabled: Whether idle qubits insertion is enabled.

        Returns:
            A list of dictionaries for each job that can be mapped to T, containing:
                - "G_index": Index of the job in the input list.
                - "job_id": The job's identifier.
                - "mapping": A dictionary mapping nodes of G to nodes of T.
                - "T_nodes": List of T nodes used in the mapping.

        """
        used_nodes: set[int] = set()
        idle_nodes: set[int] = set()
        results: list[AssignmentMatch] = []

        for idx, job in enumerate(jobs):
            g = job.circuit_graph

            model = cp_model.CpModel()
            n_g = g.number_of_nodes()
            n_t = t.number_of_nodes()

            # Variables that assign nodes of T to each node of G
            mapping = [model.new_int_var(0, n_t - 1, f"map_{i}") for i in range(n_g)]
            model.add_all_different(mapping)

            # Add constraints to prevent used nodes from being assigned
            for m in mapping:
                for used in used_nodes:
                    model.add(m != used)

            if idle_qubits_insertion_enabled:
                # Calculate idle nodes that should be avoided for mapping.
                current_idle_nodes = (
                    idle_nodes
                    | self._calculate_idle_nodes_before_mapping(
                        used_nodes, idle_nodes, inferred_topology, g
                    )
                )
                for m in mapping:
                    for node in current_idle_nodes:
                        model.add(m != node)

            # Get the set of edges in T and create allowed pairs for mapping
            t_edges_set = set(t.edges())
            # if undirected graph (direction of qubit connections does not matter),
            # uncomment the following line to add reverse edges
            allowed_pairs = list(t_edges_set)  # + [(b, a) for (a, b) in t_edges_set]

            # Add constraints to ensure edges in G map to edges in T
            for u, v in g.edges():
                model.add_allowed_assignments(
                    [mapping[int(u)], mapping[int(v)]], allowed_pairs
                )

            # run solver
            solver = cp_model.CpSolver()
            status = solver.Solve(model)
            logger.debug(
                "running solver",
                extra={
                    "job_id": job.job_id,
                    "status": status,
                },
            )

            if status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                result_mapping = {i: solver.Value(mapping[i]) for i in range(n_g)}
                mapped_t_nodes = list(set(result_mapping.values()))

                results.append(
                    AssignmentMatch(idx, job.job_id, result_mapping, mapped_t_nodes)
                )

                if idle_qubits_insertion_enabled:
                    # Calculate idle nodes that should be avoided for mapping.
                    idle_nodes.update(
                        self._calculate_idle_nodes_after_mapping(
                            used_nodes, idle_nodes, inferred_topology, g, result_mapping
                        )
                    )
                used_nodes.update(mapped_t_nodes)
            else:
                logger.debug(
                    "job does not match",
                    extra={
                        "job_id": job.job_id,
                        "index": idx,
                    },
                )

        return results
