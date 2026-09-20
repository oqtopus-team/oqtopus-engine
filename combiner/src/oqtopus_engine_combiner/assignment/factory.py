from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oqtopus_engine_combiner.assignment.base import AssignmentStrategy
from oqtopus_engine_combiner.assignment.cpsat import CpsatAssignmentStrategy
from oqtopus_engine_combiner.assignment.cpsat_window import (
    CpsatWindowAssignmentStrategy,
)
from oqtopus_engine_combiner.assignment.heuristic import HeuristicAssignmentStrategy

ASSIGNMENT_STRATEGY_NAMES = ["cpsat", "cpsat-window", "heuristic"]


def build_assignment_strategy(  # noqa: PLR0913
    name: str,
    *,
    mode: str = "backtrack",
    max_backtracks: int = 1000,
    window_multiplier: int = 4,
    min_window: int = 32,
    verify: bool = False,
) -> AssignmentStrategy:
    """Build an assignment strategy from its configured name.

    Raises:
        ValueError: If ``name`` is not a supported strategy.

    Returns:
        The configured assignment strategy.

    """
    if name == "cpsat":
        return CpsatAssignmentStrategy(verify=verify)
    if name == "cpsat-window":
        return CpsatWindowAssignmentStrategy(
            window_multiplier=window_multiplier,
            min_window=min_window,
            verify=verify,
        )
    if name in {"heuristic", "heuristics"}:
        return HeuristicAssignmentStrategy(
            mode=mode,
            max_backtracks=max_backtracks,
            verify=verify,
        )
    message = f"unknown assignment strategy: {name}"
    raise ValueError(message)
