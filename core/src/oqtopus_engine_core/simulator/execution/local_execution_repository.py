import asyncio
import hashlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .execution_repository import ExecutionRecord
from .models import ExecutionState

# ruff: noqa: DOC201, PLR0913, PLR0917, PLR6301

_TERMINAL_STATES = {
    ExecutionState.CANCELLED,
    ExecutionState.FAILED,
    ExecutionState.SUCCEEDED,
}


class LocalExecutionRepository:
    """Persist execution state for internal estimation child jobs."""

    def __init__(self, work_root: str | Path) -> None:
        self._work_root = Path(work_root).expanduser()

    async def initialize(self) -> None:
        """Create the local artifact root."""
        await asyncio.to_thread(
            self._work_root.mkdir,
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

    async def claim(self, cloud_job_id: str, job_type: str) -> bool:
        """Create a local execution record if it does not exist."""
        return await asyncio.to_thread(self._claim, cloud_job_id, job_type)

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
        """Persist and validate the canonical child execution inputs."""
        return await asyncio.to_thread(
            self._prepare,
            cloud_job_id,
            request_hash,
            options,
            work_dir,
            request_path,
            result_path,
        )

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
        """Advance one local child execution record."""
        _ = output_files, message, execution_time
        return await asyncio.to_thread(
            self._update,
            cloud_job_id,
            state,
            expected,
            slurm_job_id,
            cloud_status,
            last_error,
        )

    async def get(self, cloud_job_id: str) -> ExecutionRecord | None:
        """Return one persisted local execution record."""
        return await asyncio.to_thread(self._get, cloud_job_id)

    async def list_unfinished(self) -> list[ExecutionRecord]:
        """Return no root jobs to the Cloud-backed recovery fetcher."""
        return []

    async def list_cleanup_candidates(
        self,
        finalized_before: datetime,
    ) -> list[ExecutionRecord]:
        """Return no records to the Cloud-backed cleanup fetcher."""
        _ = finalized_before
        return []

    async def cleanup_artifacts(
        self,
        cloud_job_id: str,
        work_root: str | Path,
    ) -> bool:
        """Remove one terminal child work directory."""
        return await asyncio.to_thread(
            self._cleanup_artifacts,
            cloud_job_id,
            work_root,
        )

    def _claim(self, cloud_job_id: str, job_type: str) -> bool:
        self._ensure_root()
        metadata_path = self._metadata_path(cloud_job_id)
        if metadata_path.exists():
            return False
        now = _now()
        record = ExecutionRecord(
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
        self._write_record(record)
        return True

    def _prepare(
        self,
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
        if record.request_hash is not None:
            return record
        updated = record.model_copy(
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
        self._write_record(updated)
        return updated

    def _update(
        self,
        cloud_job_id: str,
        state: ExecutionState,
        expected: set[ExecutionState] | None,
        slurm_job_id: str | None,
        cloud_status: str | None,
        last_error: str | None,
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
        self._write_record(updated)
        return updated

    def _get(self, cloud_job_id: str) -> ExecutionRecord | None:
        metadata_path = self._metadata_path(cloud_job_id)
        if not metadata_path.is_file():
            return None
        return ExecutionRecord.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )

    def _cleanup_artifacts(
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
        configured_root = self._work_root.resolve()
        supplied_root = Path(work_root).expanduser().resolve()
        if supplied_root != configured_root:
            message = f"unexpected SLURM work root: {supplied_root}"
            raise ValueError(message)
        work_dir = Path(record.work_dir)
        if work_dir.is_symlink() or work_dir.resolve().parent != configured_root:
            message = f"refusing to clean unsafe SLURM work directory: {work_dir}"
            raise ValueError(message)
        if work_dir.exists():
            shutil.rmtree(work_dir)
        updated = record.model_copy(
            update={
                "work_dir": None,
                "request_path": None,
                "result_path": None,
                "artifact_retained": False,
                "updated_at": _now(),
                "revision": record.revision + 1,
            }
        )
        self._write_record(updated)
        return True

    def _require(self, cloud_job_id: str) -> ExecutionRecord:
        record = self._get(cloud_job_id)
        if record is None:
            message = f"execution not found: {cloud_job_id}"
            raise KeyError(message)
        return record

    def _ensure_root(self) -> None:
        self._work_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _metadata_path(self, cloud_job_id: str) -> Path:
        token = hashlib.sha256(cloud_job_id.encode()).hexdigest()[:32]
        return self._work_root / f"{token}.execution.json"

    def _write_record(self, record: ExecutionRecord) -> None:
        self._ensure_root()
        path = self._metadata_path(record.cloud_job_id)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        with temporary_path.open("w", encoding="utf-8") as stream:
            stream.write(record.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
