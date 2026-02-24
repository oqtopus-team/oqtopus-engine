from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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

    model_config = {"arbitrary_types_allowed": True}

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
    parent: "Job | None" = None
    children: list["Job"] = Field(default_factory=list)

    def __repr__(self) -> str:
        """Return a string representation excluding linked jobs.

        Returns:
            str: Formal string representation of the Job object.

        """
        attrs = []
        for key, value in self.__dict__.items():
            if key == "parent" and value is not None:
                # Removed quotes to match Pydantic style
                attrs.append(f"parent={value.job_id}")
            elif key == "children" and value:
                child_ids = [child.job_id for child in value]
                attrs.append(f"children={child_ids}")
            elif key not in {"parent", "children"}:
                # Use direct value instead of !r to avoid quotes
                attrs.append(f"{key}={value}")
        
        return f"{self.__class__.__name__}({', '.join(attrs)})"

    def __str__(self) -> str:
        """Return the custom repr string.

        Returns:
            str: Formal string representation of the Job object.

        """
        return self.__repr__()
