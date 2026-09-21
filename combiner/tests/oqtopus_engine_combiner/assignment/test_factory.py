import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[3].joinpath("src")))

from oqtopus_engine_combiner.assignment.cpsat import CpsatAssignmentStrategy
from oqtopus_engine_combiner.assignment.cpsat_window import (
    CpsatWindowAssignmentStrategy,
)
from oqtopus_engine_combiner.assignment.factory import (
    ASSIGNMENT_STRATEGY_NAMES,
    build_assignment_strategy,
)
from oqtopus_engine_combiner.assignment.heuristic import HeuristicAssignmentStrategy


class TestBuildAssignmentStrategy:
    """Tests for build_assignment_strategy."""

    def test_assignment_strategy_names(self):
        assert ASSIGNMENT_STRATEGY_NAMES == ["cpsat", "cpsat-window", "heuristic"]

    def test_builds_cpsat(self):
        strategy = build_assignment_strategy("cpsat")

        assert isinstance(strategy, CpsatAssignmentStrategy)

    def test_builds_cpsat_window(self):
        strategy = build_assignment_strategy(
            "cpsat-window", window_multiplier=8, min_window=16
        )

        assert isinstance(strategy, CpsatWindowAssignmentStrategy)
        assert strategy._window_multiplier == 8
        assert strategy._min_window == 16

    @pytest.mark.parametrize("name", ["heuristic", "heuristics"])
    def test_builds_heuristic(self, name):
        strategy = build_assignment_strategy(
            name, mode="greedy", max_backtracks=42
        )

        assert isinstance(strategy, HeuristicAssignmentStrategy)
        assert strategy._max_backtracks == 0  # greedy mode forces 0 backtracks

    def test_builds_heuristic_backtrack_mode(self):
        strategy = build_assignment_strategy(
            "heuristic", mode="backtrack", max_backtracks=42
        )

        assert isinstance(strategy, HeuristicAssignmentStrategy)
        assert strategy._max_backtracks == 42

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="unknown assignment strategy"):
            build_assignment_strategy("unknown")
