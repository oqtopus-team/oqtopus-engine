import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3].joinpath("src")))

from oqtopus_engine_combiner.assignment.cpsat import CpsatAssignmentStrategy
from oqtopus_engine_combiner.mp_auto import (  # type: ignore[import-untyped]
    JobWithCircuitGraph,
    OptimalCircuitCombiner,
)

from tests.oqtopus_engine_combiner.assignment.strategy_contract import (
    AssignmentStrategyContractTests,
)
from tests.oqtopus_engine_combiner.topology_helpers import (
    SIMPLE_2Q_QASM,
    make_linear_topology,
)


class TestCpsatAssignmentStrategy(AssignmentStrategyContractTests):
    """Tests for CpsatAssignmentStrategy.assign."""

    def build_strategy(self) -> CpsatAssignmentStrategy:
        return CpsatAssignmentStrategy()

    def test_name(self):
        assert CpsatAssignmentStrategy().name == "cpsat"
