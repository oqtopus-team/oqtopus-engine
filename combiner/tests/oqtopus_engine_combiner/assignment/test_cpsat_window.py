import sys
from pathlib import Path

import networkx as nx  # type: ignore[import-untyped]

sys.path.append(str(Path(__file__).resolve().parents[3].joinpath("src")))

from oqtopus_engine_combiner.assignment.cpsat_window import (
    CpsatWindowAssignmentStrategy,
)
from oqtopus_engine_combiner.mp_auto import (  # type: ignore[import-untyped]
    JobWithCircuitGraph,
    OptimalCircuitCombiner,
)

from tests.oqtopus_engine_combiner.assignment.strategy_contract import (
    AssignmentStrategyContractTests,
    IdleQubitsUnsupportedContractTests,
)
from tests.oqtopus_engine_combiner.topology_helpers import (
    SIMPLE_2Q_QASM,
    SIMPLE_3Q_QASM,
    make_grid_topology_with_defects,
    make_linear_topology,
)


class TestCpsatWindowAssignmentStrategy(
    AssignmentStrategyContractTests, IdleQubitsUnsupportedContractTests
):
    """Tests for CpsatWindowAssignmentStrategy.assign."""

    def build_strategy(self) -> CpsatWindowAssignmentStrategy:
        return CpsatWindowAssignmentStrategy()

    def test_idle_qubits_insertion_avoids_neighboring_nodes(self):
        # This test is intentionally left blank because the CPSAT window
        # strategy does not support idle qubits insertion yet.
        # Remove this function once idle qubits insertion is supported.
        pass

    def test_name(self):
        assert CpsatWindowAssignmentStrategy().name == "cpsat-window"

    def test_small_window_still_finds_match_via_exact_fallback(self):
        """A window smaller than the topology should still find a match via fallback."""
        topology = OptimalCircuitCombiner.create_topology_graph(
            make_grid_topology_with_defects()
        )
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_3Q_QASM)

        strategy = CpsatWindowAssignmentStrategy(
            window_multiplier=1, min_window=1, exact_fallback=True
        )
        results = strategy.assign(topology, [job])

        assert len(results) == 1

    def test_assign_with_verify_enabled_validates_mapping(self):
        topology = OptimalCircuitCombiner.create_topology_graph(make_linear_topology(5))
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        results = CpsatWindowAssignmentStrategy(verify=True).assign(topology, [job])

        assert len(results) == 1

    def test_solve_windowed_breaks_after_max_seed_attempts(self):
        """No topology edges means every window collects only its own seed node."""
        strategy = CpsatWindowAssignmentStrategy(
            window_multiplier=1, min_window=1, max_seed_attempts=1, exact_fallback=False
        )
        topology = nx.Graph()
        topology.add_nodes_from([0, 1])

        result = strategy._solve_windowed(
            t_edges={(0, 1)},
            topology=topology,
            all_nodes=[0, 1],
            free={0, 1},
            n_g=2,
            edges=[(0, 1)],
        )

        assert result is None

    def test_solve_windowed_falls_back_to_exact_when_local_windows_fail(self):
        """Local BFS windows never connect the pair, but the full free set does."""
        strategy = CpsatWindowAssignmentStrategy(window_multiplier=1, min_window=1)
        topology = nx.Graph()
        # Node 2 is isolated so it inflates len(free) without helping any window.
        topology.add_nodes_from([0, 1, 2])

        result = strategy._solve_windowed(
            t_edges={(0, 1)},
            topology=topology,
            all_nodes=[0, 1, 2],
            free={0, 1, 2},
            n_g=2,
            edges=[(0, 1)],
        )

        assert result == {0: 0, 1: 1}

    def test_solve_on_nodes_returns_none_without_allowed_pairs(self):
        strategy = CpsatWindowAssignmentStrategy()

        result = strategy._solve_on_nodes(
            t_edges=set(), n_g=2, edges=[(0, 1)], candidates=[0, 1]
        )

        assert result is None
