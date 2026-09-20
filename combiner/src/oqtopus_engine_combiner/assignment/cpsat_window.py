from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

import networkx as nx  # type: ignore[import-untyped]
from ortools.sat.python import cp_model

from oqtopus_engine_combiner.assignment.base import AssignmentMatch, validate_assignment

if TYPE_CHECKING:
    from oqtopus_engine_combiner.mp_auto import JobWithCircuitGraph

logger = logging.getLogger(__name__)


class CpsatWindowAssignmentStrategy:
    """Solve CP-SAT on local windows of free topology nodes."""

    name = "cpsat-window"

    def __init__(
        self,
        *,
        window_multiplier: int = 4,
        min_window: int = 32,
        max_seed_attempts: int = 512,
        exact_fallback: bool = True,
        verify: bool = False,
    ) -> None:
        self._window_multiplier = int(window_multiplier)
        self._min_window = int(min_window)
        self._max_seed_attempts = int(max_seed_attempts)
        self._exact_fallback = exact_fallback
        self._verify = verify

    def assign(
        self,
        t: nx.Graph,
        jobs: list[JobWithCircuitGraph],
        inferred_topology: nx.Graph | None = None,  # noqa: ARG002
        *,
        idle_qubits_insertion_enabled: bool = False,
    ) -> list[AssignmentMatch]:
        """Find circuit embeddings using local topology windows.

        Returns:
            A list of non-overlapping circuit-to-topology assignments.

        """
        if idle_qubits_insertion_enabled:
            message = "cpsat-window strategy does not support idle qubit insertion yet"
            raise NotImplementedError(message)
        logger.debug("Starting assignment with cpsat-window strategy")
        # Hoist topology preprocessing outside the per-job loop.
        # The window search uses an undirected graph only to find local candidates;
        # the CP-SAT model below still checks the original directed edges.
        t_edges = set(t.edges())
        undirected = nx.Graph()
        undirected.add_nodes_from(t.nodes())
        undirected.add_edges_from(t_edges)
        all_nodes = sorted(t.nodes())
        used: set[int] = set()
        results: list[AssignmentMatch] = []
        for idx, job in enumerate(jobs):
            n_g = job.circuit_graph.number_of_nodes()
            free = set(all_nodes) - used
            if not n_g or len(free) < n_g:
                continue
            # Collapse duplicate MultiDiGraph edges to reduce table constraints.
            mapping = self._solve_windowed(
                t_edges,
                undirected,
                all_nodes,
                free,
                n_g,
                sorted(set(job.circuit_graph.edges())),
            )
            if mapping is None:
                continue
            if self._verify:
                validate_assignment(t, job.circuit_graph, mapping, used)
            used.update(mapping.values())
            results.append(
                AssignmentMatch(idx, job.job_id, mapping, list(mapping.values()))
            )
        return results

    def _solve_windowed(  # noqa: PLR0913, PLR0917
        self,
        t_edges: set[tuple[int, int]],
        topology: nx.Graph,
        all_nodes: list[int],
        free: set[int],
        n_g: int,
        edges: list[tuple[int, int]],
    ) -> dict[int, int] | None:
        window = max(n_g * self._window_multiplier, self._min_window)
        attempts = 0
        # Try local regions first, then retain exact satisfiability with a full
        # fallback.
        for seed in all_nodes:
            if seed not in free:
                continue
            if attempts >= self._max_seed_attempts:
                break
            attempts += 1
            candidates = self._bfs_free_window(topology, free, seed, window)
            if len(candidates) >= n_g:
                result = self._solve_on_nodes(t_edges, n_g, edges, candidates)
                if result is not None:
                    return result
        if self._exact_fallback and window < len(free):
            # Preserve exact satisfiability by trying the complete free-node set once.
            return self._solve_on_nodes(t_edges, n_g, edges, sorted(free))
        return None

    @staticmethod
    def _bfs_free_window(
        topology: nx.Graph, free: set[int], seed: int, size: int
    ) -> list[int]:
        """Collect free nodes by breadth-first traversal from ``seed``.

        Returns:
            Up to ``size`` free topology nodes.

        """
        collected: list[int] = []
        seen = {seed}
        queue = deque([seed])
        # Used nodes may be traversed, but only free nodes are added to the window.
        while queue and len(collected) < size:
            node = queue.popleft()
            if node in free:
                collected.append(node)
            for neighbor in topology.neighbors(node):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return collected

    @staticmethod
    def _solve_on_nodes(
        t_edges: set[tuple[int, int]],
        n_g: int,
        edges: list[tuple[int, int]],
        candidates: list[int],
    ) -> dict[int, int] | None:
        """Solve the embedding using only candidate physical nodes.

        Returns:
            A circuit-to-topology mapping, or ``None`` when no embedding exists.

        """
        candidate_set = set(candidates)
        # Restrict variable domains and allowed pairs to the current local window.
        allowed_pairs = [
            (u, v) for u, v in t_edges if u in candidate_set and v in candidate_set
        ]
        if edges and not allowed_pairs:
            return None
        model = cp_model.CpModel()
        # Limiting the domains removes the old O(n_g * |used|) exclusion constraints.
        domain = cp_model.Domain.from_values(sorted(candidate_set))
        mapping = [
            model.new_int_var_from_domain(domain, f"map_{i}") for i in range(n_g)
        ]
        model.add_all_different(mapping)
        for u, v in edges:
            model.add_allowed_assignments(
                [mapping[int(u)], mapping[int(v)]], allowed_pairs
            )
        solver = cp_model.CpSolver()
        status = solver.solve(model)
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            return None
        return {i: solver.value(mapping[i]) for i in range(n_g)}
