import sys
from pathlib import Path

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
    SIMPLE_3Q_QASM,
    make_grid_topology_with_defects,
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
