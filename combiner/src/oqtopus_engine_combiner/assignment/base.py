from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import networkx as nx  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from oqtopus_engine_combiner.mp_auto import JobWithCircuitGraph

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssignmentMatch:
    """A circuit-to-topology embedding returned by an assignment strategy."""

    G_index: int
    job_id: str
    mapping: dict[int, int]
    T_nodes: list[int]

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401
        """Keep compatibility with the former dictionary-shaped result.

        Returns:
            The requested match attribute.

        """
        return getattr(self, key)


class AssignmentStrategy(Protocol):
    """Interface implemented by each circuit assignment algorithm."""

    name: str

    def assign(
        self,
        t: nx.Graph,
        jobs: list[JobWithCircuitGraph],
        inferred_topology: nx.Graph | None = None,
        *,
        idle_qubits_insertion_enabled: bool = False,
    ) -> list[AssignmentMatch]:
        """Find non-overlapping circuit embeddings."""


class AssignmentStrategyBase:
    """Shared helpers for assignment strategies."""

    @staticmethod
    def _calculate_idle_nodes_before_mapping(
        used_nodes: set[int],
        exist_idle_nodes: set[int],
        inferred_topology: nx.Graph | None,
        g: nx.Graph,
    ) -> set[int]:
        """Calculate idle nodes that should be avoided before mapping.

        Returns:
            Idle topology nodes that should not be used for the mapping.

        """
        g_undirected = g.to_undirected()
        if not g_undirected.number_of_edges() > 0:
            return set()

        if len(used_nodes) == 0:
            if len(exist_idle_nodes) > 0:
                logger.info(
                    "Exist idle nodes but no used nodes.",
                    extra={
                        "exist_idle_nodes": exist_idle_nodes,
                        "used_nodes": used_nodes,
                    },
                )
            return set()

        idle_nodes = set()
        if inferred_topology is not None:
            undirected_inferred_t = inferred_topology.to_undirected()
            for node in used_nodes:
                idle_nodes.update(undirected_inferred_t.neighbors(node))
        else:
            logger.info(
                "Inferred topology is None, cannot calculate idle nodes before mapping"
            )

        idle_nodes.difference_update(used_nodes | exist_idle_nodes)

        return idle_nodes

    @staticmethod
    def _calculate_idle_nodes_after_mapping(
        used_nodes: set[int],
        exist_idle_nodes: set[int],
        inferred_topology: nx.Graph | None,
        g: nx.Graph,
        result_mapping: dict[int, int],
    ) -> set[int]:
        """Calculate idle nodes that should be avoided after mapping.

        Returns:
            Idle topology nodes that should not be used for the next mapping.

        """
        g_undirected = g.to_undirected()
        if not g_undirected.number_of_edges() > 0:
            return set()

        edge_endpoints = set()
        for u, v in g_undirected.edges():
            edge_endpoints.add(u)
            edge_endpoints.add(v)

        assigned_endpoint_nodes = set()
        for edge_node in edge_endpoints:
            value = result_mapping.get(edge_node)
            if value is not None:
                assigned_endpoint_nodes.add(value)
            else:
                logger.info(
                    "Edge endpoint not in result mapping, this should not happen",
                    extra={
                        "edge_node": edge_node,
                        "result_mapping": result_mapping,
                    },
                )

        idle_nodes = set()
        if inferred_topology is not None:
            undirected_inferred_t = inferred_topology.to_undirected()
            for node in assigned_endpoint_nodes:
                idle_nodes.update(undirected_inferred_t.neighbors(node))
        else:
            logger.info(
                "Inferred topology is None, cannot calculate idle nodes after mapping"
            )

        excluded_idle_nodes = (
            used_nodes | exist_idle_nodes | set(result_mapping.values())
        )
        idle_nodes.difference_update(excluded_idle_nodes)

        return idle_nodes


def validate_assignment(
    t: nx.Graph, g: nx.Graph, mapping: dict[int, int], used_before: set[int]
) -> None:
    """Validate a subgraph embedding when strategy verification is enabled.

    Raises:
        AssertionError: If the mapping is not a valid non-overlapping embedding.

    """
    t_edges = set(t.edges())
    if set(mapping) != set(g.nodes()):
        message = "mapping does not cover the circuit graph"
        raise AssertionError(message)
    if len(mapping) != len(set(mapping.values())):
        message = "mapping assigns a physical node more than once"
        raise AssertionError(message)
    if not all((mapping[u], mapping[v]) in t_edges for u, v in g.edges()):
        message = "mapping contains a non-existent topology edge"
        raise AssertionError(message)
    if set(mapping.values()).intersection(used_before):
        message = "mapping overlaps previously used topology nodes"
        raise AssertionError(message)
