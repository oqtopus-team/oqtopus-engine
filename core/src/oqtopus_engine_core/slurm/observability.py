from datetime import datetime

from opentelemetry import metrics

from .models import ExecutionState

_meter = metrics.get_meter(__name__)

slurm_submission_counter = _meter.create_counter(
    name="oqtopus.slurm.submissions",
    description="Number of SLURM allocations submitted by Core",
    unit="1",
)
slurm_command_error_counter = _meter.create_counter(
    name="oqtopus.slurm.command.errors",
    description="Number of failed SLURM command invocations",
    unit="1",
)
slurm_cancellation_counter = _meter.create_counter(
    name="oqtopus.slurm.cancellations",
    description="Number of SLURM cancellation commands accepted",
    unit="1",
)
slurm_recovery_counter = _meter.create_counter(
    name="oqtopus.slurm.recoveries",
    description="Number of unfinished executions recovered from Cloud jobs",
    unit="1",
)
slurm_active_allocations = _meter.create_up_down_counter(
    name="oqtopus.slurm.allocations.active",
    description="Current Cloud-owned submitted SLURM allocations",
    unit="1",
)
slurm_execution_duration = _meter.create_histogram(
    name="oqtopus.slurm.execution.duration",
    description="Elapsed time from Cloud claim to a terminal execution state",
    unit="s",
)

_ACTIVE_STATES = {ExecutionState.RUNNING}
_TERMINAL_STATES = {
    ExecutionState.CANCELLED,
    ExecutionState.FAILED,
    ExecutionState.SUCCEEDED,
}


def record_execution_state_transition(
    previous: ExecutionState,
    current: ExecutionState,
    created_at: str,
    updated_at: str,
) -> None:
    """Record active-allocation deltas and terminal execution duration."""
    if previous is current:
        return
    if previous in _ACTIVE_STATES:
        slurm_active_allocations.add(-1, {"oqtopus.slurm.state": previous.value})
    if current in _ACTIVE_STATES:
        slurm_active_allocations.add(1, {"oqtopus.slurm.state": current.value})
    if current in _TERMINAL_STATES and previous not in _TERMINAL_STATES:
        duration = datetime.fromisoformat(updated_at) - datetime.fromisoformat(
            created_at
        )
        slurm_execution_duration.record(
            max(duration.total_seconds(), 0.0),
            {"oqtopus.slurm.terminal_state": current.value},
        )
