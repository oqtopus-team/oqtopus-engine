import asyncio
import hashlib
import shutil
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol, TypeVar

from oqtopus_engine_core.framework import Job
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    ApiClient,
    Configuration,
    JobsApi,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJob,
    JobsJobDef,
    JobsJobStatusUpdate,
    JobsJobStatusUpdateResponse,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.simulator.scheduler.slurm.observability import (
    record_execution_state_transition,
)

from .execution_repository import ExecutionRecord, ExecutionRepository
from .models import ExecutionState

# ruff: noqa: DOC201, DOC501
T = TypeVar("T")
_HTTP_NOT_FOUND = 404
_TERMINAL_STATES = {
    ExecutionState.CANCELLED,
    ExecutionState.FAILED,
    ExecutionState.SUCCEEDED,
}


class ExecutionJobsApi(Protocol):
    """Generated Jobs API subset required by the execution repository."""

    def get_job(
        self,
        *,
        job_id: str,
        **kwargs: object,
    ) -> JobsJob:
        """Get one job."""
        ...

    def get_jobs(
        self,
        *,
        device_id: str,
        status: str | None = None,
        **kwargs: object,
    ) -> list[JobsJob]:
        """List jobs for one device."""
        ...

    def patch_job(
        self,
        *,
        job_id: str,
        body: JobsJobStatusUpdate,
        **kwargs: object,
    ) -> JobsJobStatusUpdateResponse:
        """Update one job's existing status and result fields."""
        ...


class OqtopusCloudExecutionRepository(ExecutionRepository):
    """Derive scheduler execution views from Cloud jobs and local artifacts."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        device_id: str,
        work_root: str,
        url: str = "http://localhost:8888",
        api_key: str = "",
        proxy: str | None = None,
        workers: int = 5,
        api_request_timeout_seconds: int = 10,
        jobs_api: ExecutionJobsApi | None = None,
    ) -> None:
        if jobs_api is None:
            configuration = Configuration()
            configuration.host = url
            if proxy:
                configuration.proxy = proxy
            api_client = ApiClient(
                configuration=configuration,
                header_name="x-api-key",
                header_value=api_key,
            )
            jobs_api = JobsApi(api_client=api_client)
        self._jobs_api = jobs_api
        self._device_id = device_id
        self._work_root = Path(work_root).expanduser()
        self._request_timeout = api_request_timeout_seconds
        self._semaphore = asyncio.Semaphore(workers)

    async def initialize(self) -> None:
        """Cloud owns schema initialization; no local setup is required."""

    async def claim(self, cloud_job_id: str, job_type: str) -> bool:  # noqa: ARG002
        """Claim a ready Cloud job by advancing its existing status."""
        current = await self._require(cloud_job_id)
        if current.state is not ExecutionState.READY:
            return False
        body = JobsJobStatusUpdate(status="running")
        try:
            await self._request(
                lambda: self._jobs_api.patch_job(
                    job_id=cloud_job_id,
                    body=body,
                    _request_timeout=self._request_timeout,
                )
            )
        except Exception:
            existing = await self._require(cloud_job_id)
            if existing.cloud_status == "running":
                return True
            raise
        return True

    async def prepare(  # noqa: PLR0913
        self,
        *,
        cloud_job_id: str,
        request_hash: str,
        options: dict[str, Any],
        work_dir: Path,
        request_path: Path,
        result_path: Path,
    ) -> ExecutionRecord:
        """Validate derived local paths without adding Cloud metadata."""
        _ = request_hash, options
        expected_paths = self._local_paths(cloud_job_id)
        supplied_paths = (work_dir, request_path, result_path)
        if tuple(path.resolve() for path in supplied_paths) != tuple(
            path.resolve() for path in expected_paths
        ):
            message = f"unexpected local artifact paths for {cloud_job_id}"
            raise ValueError(message)
        current = await self.get(cloud_job_id)
        if current is None:
            message = f"execution is not claimed: {cloud_job_id}"
            raise KeyError(message)
        return current

    async def update(  # noqa: PLR0913
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
        """Update existing Cloud job fields and derive execution state."""
        _ = slurm_job_id
        current = await self._require(cloud_job_id)
        if expected is not None and current.state not in expected:
            message = (
                f"unexpected execution state for {cloud_job_id}: {current.state.value}"
            )
            raise RuntimeError(message)
        updated = await self._patch(
            current,
            state=state,
            cloud_status=cloud_status,
            last_error=last_error,
            output_files=output_files,
            message=message,
            execution_time=execution_time,
        )
        record_execution_state_transition(
            current.state,
            updated.state,
            current.created_at,
            updated.updated_at,
        )
        return updated

    async def get(self, cloud_job_id: str) -> ExecutionRecord | None:
        """Return one execution record from Cloud."""
        response = await self._get_cloud_job(cloud_job_id)
        return self._to_record(response) if response is not None else None

    async def get_job(self, cloud_job_id: str) -> Job | None:
        """Return one complete Cloud job for SLURM reconciliation."""
        response = await self._get_cloud_job(cloud_job_id)
        return Job(**response.to_dict()) if response is not None else None

    async def _get_cloud_job(self, cloud_job_id: str) -> JobsJob | None:
        try:
            response = await self._request(
                lambda: self._jobs_api.get_job(
                    job_id=cloud_job_id,
                    _request_timeout=self._request_timeout,
                )
            )
        except ApiException as error:
            if error.status == _HTTP_NOT_FOUND:
                return None
            raise
        if response.device_id != self._device_id:
            message = f"execution belongs to another device: {response.job_id}"
            raise ValueError(message)
        return response

    async def list_unfinished(self) -> list[ExecutionRecord]:
        """Return recoverable executions for this device."""
        records: list[ExecutionRecord] = []
        for status in ("running", "cancelling"):
            call: Callable[[], list[JobsJob]] = partial(
                self._jobs_api.get_jobs,
                device_id=self._device_id,
                status=status,
                _request_timeout=self._request_timeout,
            )
            response = await self._request(call)
            records.extend(self._to_record(item) for item in response)
        return records

    async def list_cleanup_candidates(
        self,
        finalized_before: datetime,
    ) -> list[ExecutionRecord]:
        """Return synchronized terminal executions with retained artifacts."""
        if finalized_before.tzinfo is None:
            message = "artifact cleanup cutoff must be timezone-aware"
            raise ValueError(message)
        records: list[ExecutionRecord] = []
        for status in ("succeeded", "failed", "cancelled"):
            call: Callable[[], list[JobsJob]] = partial(
                self._jobs_api.get_jobs,
                device_id=self._device_id,
                status=status,
                _request_timeout=self._request_timeout,
            )
            response = await self._request(call)
            records.extend(self._to_record(item) for item in response)
        return [
            record
            for record in records
            if record.artifact_retained
            and record.finalized_at is not None
            and datetime.fromisoformat(record.finalized_at) <= finalized_before
        ]

    async def cleanup_artifacts(
        self,
        cloud_job_id: str,
        work_root: str | Path,
    ) -> bool:
        """Delete local artifacts after confirming a terminal job status."""
        current = await self._require(cloud_job_id)
        if current.state not in _TERMINAL_STATES:
            message = f"cannot clean artifacts for active execution: {cloud_job_id}"
            raise RuntimeError(message)
        if not current.artifact_retained:
            return False
        await asyncio.to_thread(self._remove_work_directory, cloud_job_id, work_root)
        return True

    async def _patch(  # noqa: PLR0913
        self,
        current: ExecutionRecord,
        *,
        state: ExecutionState | None = None,
        cloud_status: str | None = None,
        last_error: str | None = None,
        output_files: list[str] | None = None,
        message: str | None = None,
        execution_time: float | None = None,
    ) -> ExecutionRecord:
        desired = {
            key: value
            for key, value in {"state": state, "cloud_status": cloud_status}.items()
            if value is not None
        }
        if self._matches(current, desired):
            return current

        if cloud_status is None:
            refreshed = await self._require(current.cloud_job_id)
            return refreshed.model_copy(update={"state": state or refreshed.state})

        request = JobsJobStatusUpdate(
            status=cloud_status,
            output_files=output_files,
            message=message or last_error,
            execution_time=execution_time,
        )
        call: Callable[[], JobsJobStatusUpdateResponse] = partial(
            self._jobs_api.patch_job,
            job_id=current.cloud_job_id,
            body=request,
            _request_timeout=self._request_timeout,
        )
        try:
            await self._request(call)
        except Exception:
            fresh = await self.get(current.cloud_job_id)
            if fresh is not None and self._matches(fresh, desired):
                return fresh
            raise
        return await self._require(current.cloud_job_id)

    async def _require(self, cloud_job_id: str) -> ExecutionRecord:
        record = await self.get(cloud_job_id)
        if record is None:
            message = f"execution not found: {cloud_job_id}"
            raise KeyError(message)
        return record

    async def _request(self, call: Callable[[], T]) -> T:
        async with self._semaphore:
            return await asyncio.to_thread(call)

    def _to_record(self, job: JobsJob | JobsJobDef) -> ExecutionRecord:
        if job.device_id != self._device_id:
            message = f"execution belongs to another device: {job.job_id}"
            raise ValueError(message)
        cloud_job_id = str(job.job_id)
        work_dir, request_path, result_path = self._local_paths(cloud_job_id)
        artifact_retained = work_dir.is_dir()
        cloud_status = str(job.status)
        state_by_status = {
            "submitted": ExecutionState.READY,
            "ready": ExecutionState.READY,
            "running": ExecutionState.RUNNING,
            "cancelling": ExecutionState.RUNNING,
            "succeeded": ExecutionState.SUCCEEDED,
            "failed": ExecutionState.FAILED,
            "cancelled": ExecutionState.CANCELLED,
        }
        state = state_by_status[cloud_status]
        if state is ExecutionState.RUNNING and result_path.is_file():
            state = ExecutionState.RESULT_READY
        created_at = job.running_at or job.ready_at or job.submitted_at
        if created_at is None:
            created_at = datetime.now(UTC)
        updated_at = job.ended_at or created_at
        return ExecutionRecord(
            cloud_job_id=cloud_job_id,
            job_type=str(job.job_type),
            state=state,
            request_hash=None,
            options=dict(job.simulator_info or {}),
            work_dir=str(work_dir) if artifact_retained else None,
            request_path=str(request_path) if artifact_retained else None,
            result_path=str(result_path) if artifact_retained else None,
            slurm_job_id=None,
            cloud_status=cloud_status,
            last_error=None,
            artifact_retained=artifact_retained,
            created_at=created_at.isoformat(),
            updated_at=updated_at.isoformat(),
            finalized_at=(
                job.ended_at.isoformat() if job.ended_at is not None else None
            ),
            revision=0,
        )

    @staticmethod
    def _matches(record: ExecutionRecord, desired: Mapping[str, object]) -> bool:
        for field, desired_value in desired.items():
            actual = getattr(record, field)
            normalized_desired = desired_value
            if isinstance(desired_value, ExecutionState):
                normalized_desired = desired_value.value
                actual = actual.value
            if isinstance(desired_value, datetime):
                normalized_desired = desired_value.isoformat()
            if actual != normalized_desired:
                return False
        return True

    def _remove_work_directory(
        self,
        cloud_job_id: str,
        work_root: str | Path,
    ) -> None:
        configured_root = self._work_root.resolve()
        supplied_root = Path(work_root).expanduser().resolve()
        if supplied_root != configured_root:
            message = f"unexpected SLURM work root: {supplied_root}"
            raise ValueError(message)
        work_dir, _, _ = self._local_paths(cloud_job_id)
        if work_dir.is_symlink() or work_dir.resolve().parent != configured_root:
            message = f"refusing to clean unsafe SLURM work directory: {work_dir}"
            raise ValueError(message)
        if work_dir.exists():
            shutil.rmtree(work_dir)

    def _local_paths(self, cloud_job_id: str) -> tuple[Path, Path, Path]:
        token = hashlib.sha256(cloud_job_id.encode()).hexdigest()[:32]
        work_dir = self._work_root / token
        return work_dir, work_dir / "request.json", work_dir / "result.json"
