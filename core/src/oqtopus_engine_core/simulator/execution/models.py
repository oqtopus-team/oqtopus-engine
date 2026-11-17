from enum import StrEnum


class ExecutionState(StrEnum):
    """Durable lifecycle states for one Cloud-to-scheduler execution."""

    READY = "ready"
    RUNNING = "running"
    RESULT_READY = "result_ready"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
