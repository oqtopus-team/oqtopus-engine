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


class JobInput(BaseModel):
    """Input payload for a job submitted to the pipeline."""

    program: list[str] | None = None
    operator: list[OperatorItem] | None = None
    sse_program: str | None = None


class Job(BaseModel):
    """Job model."""

    model_config = {"arbitrary_types_allowed": True}

    # Engine-unique execution unit ID; see docs/design/pipeline_execution.md
    # (Job ID Conventions) for how this differs from repository_job_id.
    job_id: str
    # Cloud record ID, or None if this Job has no repository entity of its
    # own. A marker only: never substitute this for job_id in a request
    # path. See docs/design/pipeline_execution.md and resolve_repository_jobs.
    repository_job_id: str | None = None
    name: str | None = None
    description: str | None = None
    device_id: str
    shots: int
    job_type: str
    input: str
    program: list[str] | None = None
    operator: list[OperatorItem] | None = None
    sse_program: str | None = None
    combined_program: str | None = None
    transpile_result: TranspileResult | None = None
    result: JobResult | None = None
    sse_log: str | None = None
    output_files: list[str] = []
    transpiler_info: dict[str, Any]
    simulator_info: dict[str, Any]
    mitigation_info: dict[str, Any]
    status: str
    message: str | None = None
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


def resolve_repository_jobs(job: Job) -> list[Job]:
    """Resolve the Job objects that a repository (Cloud) update applies to.

    Returns Job objects, not job_id strings, so callers can mutate the
    shared object directly. Already deduped by job_id (e.g. two estimation
    children of the same parent, combined together, both resolve to that
    one parent). An empty result is expected, not an error. See
    docs/design/pipeline_execution.md (Job ID Conventions) for the four
    job/repository-entity relationships this covers and why.

    Returns:
        The repository-tracked Job objects to update, deduped by job_id.

    """
    if job.repository_job_id is None:
        # Dedupe by job_id: two children can resolve to the same target
        # (e.g. two estimation children of the same parent combined
        # together), and callers must not be updated twice for it.
        unique: dict[str, Job] = {}
        for child in job.children:
            for resolved in resolve_repository_jobs(child):
                unique[resolved.job_id] = resolved
        return list(unique.values())

    current = job
    while current.job_id != current.repository_job_id and current.parent is not None:
        current = current.parent
    return [current] if current.job_id == current.repository_job_id else []
