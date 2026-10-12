from dataclasses import dataclass
from enum import StrEnum


class SchedulerState(StrEnum):
    """Engine-facing scheduler state categories."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SchedulerJobStatus:
    """Normalized status for one scheduler allocation."""

    job_id: str
    state: SchedulerState
    raw_state: str
    exit_code: str | None = None
    reason: str | None = None
