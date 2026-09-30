from oqtopus_engine_core.simulator.scheduler.models import SchedulerState

_PENDING_STATES = {
    "CONFIGURING",
    "PENDING",
    "REQUEUED",
    "REQUEUE_FED",
    "REQUEUE_HOLD",
    "RESV_DEL_HOLD",
}
_RUNNING_STATES = {
    "COMPLETING",
    "RESIZING",
    "RUNNING",
    "SIGNALING",
    "STAGE_OUT",
    "STOPPED",
    "SUSPENDED",
}
_FAILED_STATES = {
    "BOOT_FAIL",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "SPECIAL_EXIT",
    "TIMEOUT",
}


def normalize_slurm_state(raw_state: str) -> SchedulerState:
    """Map a SLURM state label to a stable engine-facing category.

    Returns:
        The normalized scheduler state.

    """
    state = raw_state.strip().upper().rstrip("+")
    if state.startswith("CANCELLED"):
        return SchedulerState.CANCELLED
    if state in _PENDING_STATES:
        return SchedulerState.PENDING
    if state in _RUNNING_STATES:
        return SchedulerState.RUNNING
    if state == "COMPLETED":
        return SchedulerState.COMPLETED
    if state in _FAILED_STATES:
        return SchedulerState.FAILED
    return SchedulerState.UNKNOWN
