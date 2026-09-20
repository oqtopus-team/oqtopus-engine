from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

import networkx as nx  # type: ignore[import-untyped]

from oqtopus_engine_combiner.assignment.base import AssignmentMatch, validate_assignment

if TYPE_CHECKING:
    from oqtopus_engine_combiner.mp_auto import JobWithCircuitGraph

logger = logging.getLogger(__name__)


class HeuristicAssignmentStrategy:
    """Embed circuits with greedy search plus bounded MRV backtracking."""

    name = "heuristic"

    def __init__(
        self,
        mode: str = "greedy",
        max_backtracks: int = 1000,
        verify: bool = False,
    ) -> None:
        if mode not in {"greedy", "backtrack"}:
            raise ValueError(f"unknown heuristic mode: {mode}")
        self._max_backtracks = 0 if mode == "greedy" else int(max_backtracks)
        self._verify = verify

    def assign(
        self,
        t: nx.Graph,
        jobs: list[JobWithCircuitGraph],
        inferred_topology: nx.Graph | None = None,
        idle_qubits_insertion_enabled: bool = False,
    ) -> list[AssignmentMatch]:
        if idle_qubits_insertion_enabled:
            raise NotImplementedError(
                "heuristic strategy does not support idle qubit insertion yet"
            )
        logger.debug("Starting assignment with heuristic strategy")
        # Hoist topology preprocessing outside the per-job embedding loop.
        edges = set(t.edges())
        successors: dict[int, set[int]] = defaultdict(set)
        predecessors: dict[int, set[int]] = defaultdict(set)
        for source, target in edges:
            successors[source].add(target)
            predecessors[target].add(source)
        nodes = sorted(t.nodes())
        degree = {
            node: len(successors[node]) + len(predecessors[node]) for node in nodes
        }
        used: set[int] = set()
        results: list[AssignmentMatch] = []
        for idx, job in enumerate(jobs):
            mapping = self._embed(
                job.circuit_graph, edges, successors, predecessors, degree, nodes, used
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

    def _embed(
        self,
        graph: nx.Graph,
        t_edges: set[tuple[int, int]],
        t_successors: dict[int, set[int]],
        t_predecessors: dict[int, set[int]],
        t_degree: dict[int, int],
        all_nodes: list[int],
        used: set[int],
    ) -> dict[int, int] | None:
        """Embed one circuit graph and return ``None`` when no embedding is found."""
        nodes = list(graph.nodes())
        if not nodes:
            return {}
        # Build directed adjacency tables for constraint propagation.
        successors: dict[int, set[int]] = {node: set() for node in nodes}
        predecessors: dict[int, set[int]] = {node: set() for node in nodes}
        for source, target in graph.edges():
            successors[source].add(target)
            predecessors[target].add(source)
        degree = {
            node: len(successors[node]) + len(predecessors[node]) for node in nodes
        }
        # Start every circuit node with all currently free physical nodes.
        free = set(all_nodes) - used
        if len(free) < len(nodes):
            return None
        domains = {node: set(free) for node in nodes}
        assigned: dict[int, int] = {}
        used_physical: set[int] = set()
        budget = [self._max_backtracks]

        def select() -> int:
            # MRV chooses the most constrained circuit node first; degree breaks ties.
            # Choosing the highest-degree node on ties makes failures surface earlier.
            return min(
                (node for node in nodes if node not in assigned),
                key=lambda node: (len(domains[node]), -degree[node]),
            )

        def candidates(node: int) -> list[int]:
            def score(physical: int) -> tuple[int, int, int]:
                # Prefer candidates that already satisfy adjacent assigned nodes.
                # The final ascending ID tie-break avoids poor starts on directed grids.
                fit = sum(
                    1
                    for neighbor in predecessors[node]
                    if neighbor in assigned
                    and (assigned[neighbor], physical) in t_edges
                )
                fit += sum(
                    1
                    for neighbor in successors[node]
                    if neighbor in assigned
                    and (physical, assigned[neighbor]) in t_edges
                )
                return (-fit, -t_degree.get(physical, 0), physical)

            return sorted(domains[node], key=score)

        def compatible(node: int, physical: int) -> bool:
            if physical in used_physical:
                return False
            for neighbor in predecessors[node]:  # edge neighbor -> node
                if (
                    neighbor in assigned
                    and (assigned[neighbor], physical) not in t_edges
                ):
                    return False
            for neighbor in successors[node]:  # edge node -> neighbor
                if (
                    neighbor in assigned
                    and (physical, assigned[neighbor]) not in t_edges
                ):
                    return False
            return True

        def search() -> dict[int, int] | None:
            if len(assigned) == len(nodes):
                return dict(assigned)
            node = select()
            for physical in candidates(node):
                if not compatible(node, physical):
                    continue
                assigned[node] = physical
                used_physical.add(physical)
                trail: list[tuple[int, set[int]]] = []
                alive = True
                # Propagate directed edge constraints and all-different constraints.
                # Store only removed values so each failed branch can be restored cheaply.
                for neighbor in successors[node]:
                    if neighbor not in assigned:
                        removed = domains[neighbor] - t_successors.get(physical, set())
                        domains[neighbor] -= removed
                        trail.append((neighbor, removed))
                        alive &= bool(domains[neighbor])
                for neighbor in predecessors[node]:
                    if neighbor not in assigned:
                        removed = domains[neighbor] - t_predecessors.get(
                            physical, set()
                        )
                        domains[neighbor] -= removed
                        trail.append((neighbor, removed))
                        alive &= bool(domains[neighbor])
                for neighbor in nodes:
                    # Remove the selected physical node from every other domain.
                    if neighbor not in assigned and physical in domains[neighbor]:
                        domains[neighbor].remove(physical)
                        trail.append((neighbor, {physical}))
                        alive &= bool(domains[neighbor])
                if alive:
                    result = search()
                    if result is not None:
                        return result
                for neighbor, removed in reversed(trail):
                    domains[neighbor].update(removed)
                used_physical.remove(physical)
                del assigned[node]
                # Stop embedding this job after the backtracking budget is exhausted.
                if budget[0] <= 0:
                    return None
                budget[0] -= 1
            return None

        return search()
