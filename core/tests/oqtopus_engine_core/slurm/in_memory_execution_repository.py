import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oqtopus_engine_core.slurm import ExecutionRecord, ExecutionState

# ruff: noqa: ASYNC240, PLR0913

_TERMINAL_STATES = {
    ExecutionState.CANCELLED,
    ExecutionState.FAILED,
    ExecutionState.SUCCEEDED,
}


class InMemorySlurmExecutionRepository:
    """SLURM execution repository test double."""

    def __init__(self, _location: object | None = None) -> None:
        self._records: dict[str, ExecutionRecord] = {}

    async def initialize(self) -> None:
        pass

    async def claim(self, cloud_job_id: str, job_type: str) -> bool:
        if cloud_job_id in self._records:
            return False
        now = _now()
        self._records[cloud_job_id] = ExecutionRecord(
            cloud_job_id=cloud_job_id,
            job_type=job_type,
            state=ExecutionState.READY,
            request_hash=None,
            options=None,
            work_dir=None,
            request_path=None,
            result_path=None,
            slurm_job_id=None,
            cloud_status=None,
            last_error=None,
            artifact_retained=False,
            created_at=now,
            updated_at=now,
            finalized_at=None,
            revision=0,
        )
        return True

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
        record = self._require(cloud_job_id)
        if record.request_hash is not None and record.request_hash != request_hash:
            message = f"execution request changed for {cloud_job_id}"
            raise ValueError(message)
        if record.request_hash is None:
            record = record.model_copy(
                update={
                    "request_hash": request_hash,
                    "options": options,
                    "work_dir": str(work_dir),
                    "request_path": str(request_path),
                    "result_path": str(result_path),
                    "artifact_retained": True,
                    "updated_at": _now(),
                    "revision": record.revision + 1,
                }
            )
            self._records[cloud_job_id] = record
        return record

    async def update(
        self,
        cloud_job_id: str,
        state: ExecutionState,
        *,
        expected: set[ExecutionState] | None = None,
        slurm_job_id: str | None = None,
        cloud_status: str | None = None,
        last_error: str | None = None,
        output_files: list[str] | None = None,  # noqa: ARG002
        message: str | None = None,  # noqa: ARG002
        execution_time: float | None = None,  # noqa: ARG002
    ) -> ExecutionRecord:
        record = self._require(cloud_job_id)
        if expected is not None and record.state not in expected:
            message = (
                f"unexpected execution state for {cloud_job_id}: {record.state.value}"
            )
            raise RuntimeError(message)
        values: dict[str, object] = {
            "state": state,
            "updated_at": _now(),
            "revision": record.revision + 1,
        }
        if slurm_job_id is not None:
            values["slurm_job_id"] = slurm_job_id
        if cloud_status is not None:
            values["cloud_status"] = cloud_status
        if last_error is not None:
            values["last_error"] = last_error
        if state in _TERMINAL_STATES and record.finalized_at is None:
            values["finalized_at"] = _now()
        updated = record.model_copy(update=values)
        self._records[cloud_job_id] = updated
        return updated

    async def get(self, cloud_job_id: str) -> ExecutionRecord | None:
        return self._records.get(cloud_job_id)

    async def list_unfinished(self) -> list[ExecutionRecord]:
        return [
            record
            for record in self._records.values()
            if record.state not in _TERMINAL_STATES
            or (
                record.state is ExecutionState.FAILED
                and record.cloud_status != "failed"
            )
            or (
                record.state is ExecutionState.CANCELLED
                and record.cloud_status != "cancelled"
            )
        ]

    async def list_cleanup_candidates(
        self,
        finalized_before: datetime,
    ) -> list[ExecutionRecord]:
        return [
            record
            for record in self._records.values()
            if record.state in _TERMINAL_STATES
            and record.cloud_status == record.state.value
            and record.artifact_retained
            and record.finalized_at is not None
            and datetime.fromisoformat(record.finalized_at) <= finalized_before
        ]

    async def cleanup_artifacts(
        self,
        cloud_job_id: str,
        work_root: str | Path,
    ) -> bool:
        record = self._require(cloud_job_id)
        if record.state not in _TERMINAL_STATES:
            message = f"cannot clean artifacts for active execution: {cloud_job_id}"
            raise RuntimeError(message)
        if not record.artifact_retained or record.work_dir is None:
            return False
        root = Path(work_root).resolve()
        work_dir = Path(record.work_dir)
        if work_dir.is_symlink() or work_dir.resolve().parent != root:
            message = f"refusing to clean unsafe SLURM work directory: {work_dir}"
            raise ValueError(message)
        if work_dir.exists():
            shutil.rmtree(work_dir)
        self._records[cloud_job_id] = record.model_copy(
            update={
                "work_dir": None,
                "request_path": None,
                "result_path": None,
                "artifact_retained": False,
                "updated_at": _now(),
                "revision": record.revision + 1,
            }
        )
        return True

    def _require(self, cloud_job_id: str) -> ExecutionRecord:
        record = self._records.get(cloud_job_id)
        if record is None:
            message = f"execution not found: {cloud_job_id}"
            raise KeyError(message)
        return record


def _now() -> str:
    return datetime.now(UTC).isoformat()
