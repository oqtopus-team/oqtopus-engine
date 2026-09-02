from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

# ruff: noqa: DOC201, DOC501
from pydantic import BaseModel, ConfigDict, Field


class SlurmState(StrEnum):
    """Engine-facing SLURM state categories."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SlurmJobStatus:
    """Normalized status for one SLURM allocation."""

    job_id: str
    state: SlurmState
    raw_state: str
    exit_code: str | None = None
    reason: str | None = None


class ExecutionState(StrEnum):
    """Durable lifecycle states for one Cloud-to-SLURM execution."""

    READY = "ready"
    RUNNING = "running"
    RESULT_READY = "result_ready"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


class SlurmSimulatorOptions(BaseModel):
    """Validated user-selectable options for the Qulacs MPI backend."""

    model_config = ConfigDict(extra="forbid", strict=True)

    backend: Literal["qulacs_mpi"] = "qulacs_mpi"
    n_nodes: int | None = Field(default=None, ge=1)
    n_per_node: int = Field(default=1, ge=1)
    seed_simulation: int | None = Field(
        default=None,
        ge=-(2**31),
        le=2**31 - 1,
    )
    timeout_seconds: int = Field(default=600, ge=1)

    def resolve(
        self,
        *,
        n_qubits: int,
        qubits_per_node: int,
        max_nodes: int,
        max_n_per_node: int,
        max_timeout_seconds: int,
    ) -> "SlurmSimulatorOptions":
        """Apply circuit-derived and administrator-configured limits."""
        if qubits_per_node < 1:
            message = "qubits_per_node must be positive"
            raise ValueError(message)
        minimum_nodes = 2 ** max(n_qubits - qubits_per_node, 0)
        n_nodes = self.n_nodes or minimum_nodes
        if n_nodes < minimum_nodes:
            message = f"n_nodes must be at least {minimum_nodes} for {n_qubits} qubits"
            raise ValueError(message)
        if n_nodes > max_nodes:
            message = f"n_nodes exceeds administrator limit {max_nodes}"
            raise ValueError(message)
        if self.n_per_node > max_n_per_node:
            message = f"n_per_node exceeds administrator limit {max_n_per_node}"
            raise ValueError(message)
        if self.timeout_seconds > max_timeout_seconds:
            message = (
                f"timeout_seconds exceeds administrator limit {max_timeout_seconds}"
            )
            raise ValueError(message)
        return self.model_copy(update={"n_nodes": n_nodes})


class OperatorTerm(BaseModel):
    """One real-valued Pauli term in a direct estimation request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pauli: str
    coeff: float


class QulacsGate(BaseModel):
    """One allowlisted Qulacs gate operation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    qubits: list[int]
    params: list[float] = Field(default_factory=list)


class QulacsExecutionRequest(BaseModel):
    """Versioned filesystem contract consumed by the MPI worker."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    job_type: Literal["sampling", "estimation"]
    n_qubits: int = Field(ge=1)
    gates: list[QulacsGate]
    measurement_mapping: dict[int, int] = Field(default_factory=dict)
    shots: int | None = Field(default=None, ge=1)
    operators: list[OperatorTerm] = Field(default_factory=list)
    seed_simulation: int | None = None
    n_per_node: int = Field(ge=1)


class QulacsExecutionResult(BaseModel):
    """Versioned raw result produced by the standalone MPI worker."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    status: Literal["succeeded"]
    job_type: Literal["sampling", "estimation"]
    counts: dict[str, int] | None = None
    exp_value: list[float] | None = None
    duration_seconds: float = Field(ge=0)


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


def normalize_slurm_state(raw_state: str) -> SlurmState:
    """Map a SLURM state label to a stable engine-facing category."""
    state = raw_state.strip().upper().rstrip("+")
    if state.startswith("CANCELLED"):
        return SlurmState.CANCELLED
    if state in _PENDING_STATES:
        return SlurmState.PENDING
    if state in _RUNNING_STATES:
        return SlurmState.RUNNING
    if state == "COMPLETED":
        return SlurmState.COMPLETED
    if state in _FAILED_STATES:
        return SlurmState.FAILED
    return SlurmState.UNKNOWN
