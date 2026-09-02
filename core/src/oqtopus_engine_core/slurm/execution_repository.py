import fcntl
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Protocol, Self

from pydantic import BaseModel, ConfigDict

from oqtopus_engine_core.framework.model import Job

from .models import ExecutionState

# ruff: noqa: DOC201, PLR0913


class ExecutionRecord(BaseModel):
    """Normalized view of one Cloud job's SLURM execution."""

    model_config = ConfigDict(frozen=True)

    cloud_job_id: str
    job_type: str
    state: ExecutionState
    request_hash: str | None
    options: dict[str, Any] | None
    work_dir: str | None
    request_path: str | None
    result_path: str | None
    slurm_job_id: str | None
    cloud_status: str | None
    last_error: str | None
    artifact_retained: bool = True
    created_at: str
    updated_at: str
    finalized_at: str | None
    revision: int


class SlurmJobReader(Protocol):
    """Single-job lookup required only by the direct SLURM runtime."""

    async def get_job(self, job_id: str) -> Job | None:
        """Return one Cloud job by identifier."""
        ...


class SlurmExecutionRepository(Protocol):
    """SLURM execution view and lifecycle repository contract."""

    async def initialize(self) -> None:
        """Initialize the repository if required by its implementation."""
        ...

    async def claim(self, cloud_job_id: str, job_type: str) -> bool:
        """Atomically claim a Cloud job if it has not been seen before."""
        ...

    async def prepare(
        self,
        *,
        cloud_job_id: str,
        request_hash: str,
        options: dict[str, Any],
        work_dir: Path,
        request_path: Path,
        result_path: Path,
    ) -> ExecutionRecord:
        """Prepare or validate canonical execution inputs."""
        ...

    async def update(
        self,
        cloud_job_id: str,
        state: ExecutionState,
        *,
        expected: set[ExecutionState] | None = None,
        slurm_job_id: str | None = None,
        cloud_status: str | None = None,
        last_error: str | None = None,
        output_files: list[str] | None = None,
        message: str | None = None,
        execution_time: float | None = None,
    ) -> ExecutionRecord:
        """Compare and update one execution record."""
        ...

    async def get(self, cloud_job_id: str) -> ExecutionRecord | None:
        """Return one execution record."""
        ...

    async def list_unfinished(self) -> list[ExecutionRecord]:
        """Return active and terminal-but-Cloud-unsynchronized executions."""
        ...

    async def list_cleanup_candidates(
        self,
        finalized_before: datetime,
    ) -> list[ExecutionRecord]:
        """Return synchronized terminal executions eligible for cleanup."""
        ...

    async def cleanup_artifacts(
        self,
        cloud_job_id: str,
        work_root: str | Path,
    ) -> bool:
        """Delete retained local artifacts for a terminal job."""
        ...


class SingleProcessLock:
    """Advisory process lock for a single-device PoC engine."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        """Acquire the non-blocking process lock.

        Raises:
            RuntimeError: If another process already holds the lock.

        """
        if self._handle is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            message = f"another SLURM simulator engine holds {self._path}"
            raise RuntimeError(message) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(__import__("os").getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        """Release the process lock if held."""
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> Self:
        """Acquire this lock for a context-managed process lifetime."""
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release this context-managed process lock."""
        self.release()
