from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Device(BaseModel):
    """Device information model."""

    device_id: str
    device_type: str
    status: str
    available_at: datetime | None = None
    n_qubits: int
    basis_gates: list[str]
    instructions: list[str]
    device_info: str | None = None
    calibrated_at: datetime | None = None
    description: str
    is_connected: bool = False


class OperatorItem(BaseModel):
    """Operator item model."""

    pauli: str
    coeff: float


class TranspileResult(BaseModel):
    """Transpilation result model."""

    transpiled_program: str
    stats: dict[str, Any]
    virtual_physical_mapping: dict[str, Any]


class SamplingResult(BaseModel):
    """Sampling result model."""

    counts: dict[str, Any] | None = None
    divided_counts: dict[str, Any] | None = None


class EstimationResult(BaseModel):
    """Estimation result model."""

    exp_value: float | None = None
    stds: float | None = None


class JobResult(BaseModel):
    """Job result model."""

    sampling: SamplingResult | None = None
    estimation: EstimationResult | None = None


class JobInfo(BaseModel):
    """Job detail information model."""

    program: list[str]
    combined_program: str | None = None
    operator: list[OperatorItem] | None = None
    result: JobResult | None = None
    transpile_result: TranspileResult | None = None
    message: str | None = None


class Job(BaseModel):
    """Job model."""

    job_id: str
    name: str | None = None
    description: str | None = None
    device_id: str
    shots: int
    job_type: str
    job_info: JobInfo
    transpiler_info: dict[str, Any]
    simulator_info: dict[str, Any]
    mitigation_info: dict[str, Any]
    status: str
    execution_time: float | None = None
    submitted_at: datetime | None = None
    ready_at: datetime | None = None
    running_at: datetime | None = None
    ended_at: datetime | None = None
