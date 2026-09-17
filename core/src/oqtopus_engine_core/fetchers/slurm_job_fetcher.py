import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ruff: noqa: DOC201, DOC501
from oqtopus_engine_core.framework import Job, JobContext
from oqtopus_engine_core.framework.job_fetcher import wait_until_fetchable
from oqtopus_engine_core.simulator.execution import (
    ExecutionRecord,
    ExecutionRepository,
    ExecutionState,
    JobReader,
)
from oqtopus_engine_core.simulator.mpi_qulacs import (
    QulacsExecutionRequest,
    SlurmSimulatorOptions,
    build_slurm_job_labels,
    request_hash,
)
from oqtopus_engine_core.simulator.scheduler import SchedulerState
from oqtopus_engine_core.simulator.scheduler.slurm import (
    SlurmClient,
    SlurmReconciliationAmbiguousError,
)
from oqtopus_engine_core.simulator.scheduler.slurm.observability import (
    slurm_recovery_counter,
)

from .repository_job_fetcher import RepositoryJobFetcher

logger = logging.getLogger(__name__)


class SlurmJobFetcher(RepositoryJobFetcher):
    """Fetch and recover jobs owned by durable Cloud execution records."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        execution_repository: ExecutionRepository,
        job_reader: JobReader,
        slurm_client: SlurmClient,
        interval_seconds: float = 5.0,
        limit: int = 10,
        job_fetch_threshold: int = 10,
        work_root: str | None = None,
        artifact_ttl_seconds: int = 604800,
        batch_script: str | None = None,
        worker_script: str | None = None,
        qubits_per_node: int = 30,
        max_nodes: int = 1024,
        max_n_per_node: int = 48,
        max_timeout_seconds: int = 432000,
    ) -> None:
        super().__init__(interval_seconds, limit, job_fetch_threshold)
        self._execution_repository = execution_repository
        self._job_reader = job_reader
        self._slurm_client = slurm_client
        self._scheduled_job_ids: set[str] = set()
        self._work_root = (
            Path(work_root).expanduser() if work_root is not None else None
        )
        self._artifact_ttl_seconds = artifact_ttl_seconds
        self._batch_script = (
            Path(batch_script).expanduser() if batch_script is not None else None
        )
        self._worker_script = (
            Path(worker_script).expanduser() if worker_script is not None else None
        )
        self._qubits_per_node = qubits_per_node
        self._max_nodes = max_nodes
        self._max_n_per_node = max_n_per_node
        self._max_timeout_seconds = max_timeout_seconds

    async def start(self) -> None:
        """Recover execution-owned work before polling Cloud for new jobs."""
        self.validate_fetcher_ready()
        gctx = self.gctx
        pipeline = self.pipeline
        if gctx is None or pipeline is None:  # pragma: no cover
            return
        if gctx.job_repository is None:
            message = "Job repository must be set before starting the fetcher."
            raise RuntimeError(message)

        self.validate_runtime_paths()
        await self._execution_repository.initialize()
        await wait_until_fetchable(
            gctx,
            pipeline,
            self._interval_seconds,
            self._job_fetch_threshold,
        )
        await self._recover_until_ready()
        while True:
            try:
                await wait_until_fetchable(
                    gctx,
                    pipeline,
                    self._interval_seconds,
                    self._job_fetch_threshold,
                )
                fetched_count = await self.poll_once()
                if fetched_count < self._limit:
                    await asyncio.sleep(self._interval_seconds)
            except Exception:
                logger.exception("unexpected error during SLURM job fetch")
                await asyncio.sleep(self._interval_seconds)

    def validate_runtime_paths(self) -> None:
        """Validate local paths required before submitting a SLURM allocation."""
        if self._work_root is None:
            message = "SLURM work root must be configured before startup."
            raise RuntimeError(message)
        work_root = self._work_root
        if not work_root.is_absolute():
            message = f"SLURM work root must be absolute: {work_root}"
            raise ValueError(message)
        work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not work_root.is_dir() or not os.access(work_root, os.W_OK | os.X_OK):
            message = f"SLURM work root is not writable: {work_root}"
            raise PermissionError(message)

        for label, configured_script, require_execute in (
            ("batch", self._batch_script, True),
            ("worker", self._worker_script, False),
        ):
            if configured_script is None:
                message = f"SLURM {label} script must be configured before startup."
                raise RuntimeError(message)
            script = configured_script
            if not script.is_absolute():
                message = f"SLURM {label} script must be absolute: {script}"
                raise ValueError(message)
            if not script.is_file() or not os.access(script, os.R_OK):
                message = f"SLURM {label} script is not readable: {script}"
                raise FileNotFoundError(message)
            if require_execute and not os.access(script, os.X_OK):
                message = f"SLURM {label} script is not executable: {script}"
                raise PermissionError(message)

    async def _recover_until_ready(self) -> None:
        while True:
            try:
                await self.recover_unfinished()
            except Exception:
                logger.exception(
                    "startup SLURM recovery failed and will retry",
                )
                await asyncio.sleep(self._interval_seconds)
            else:
                return

    async def recover_unfinished(self) -> None:
        """Requeue unfinished execution records and reconcile terminal jobs."""
        gctx = self.gctx
        if gctx is None or gctx.job_repository is None:
            message = "Job repository must be configured for recovery."
            raise RuntimeError(message)
        await self._cleanup_expired_artifacts()

        jobs: list[Job] = []
        for record in await self._execution_repository.list_unfinished():
            job = await self._job_reader.get_job(record.cloud_job_id)
            if job is None:
                logger.warning(
                    "recoverable SLURM job is missing from Cloud",
                    extra={"job_id": record.cloud_job_id},
                )
                continue
            if record.state is ExecutionState.FAILED:
                await self._reconcile_unsynchronized_terminal(job, record)
                continue
            if record.state is ExecutionState.CANCELLED:
                if await self._reconcile_terminal_job(job, record):
                    continue
                await self._reconcile_unsynchronized_terminal(job, record)
                continue
            if await self._reconcile_terminal_job(job, record):
                continue
            if record.state is ExecutionState.RESULT_READY or job.status in {
                "submitted",
                "ready",
                "running",
                "cancelling",
            }:
                jobs.append(job)
        if jobs:
            slurm_recovery_counter.add(len(jobs))
        await self._enqueue(jobs)

    async def _reconcile_unsynchronized_terminal(
        self,
        job: Job,
        record: ExecutionRecord,
    ) -> None:
        cloud_status = (
            "cancelled" if record.state is ExecutionState.CANCELLED else "failed"
        )
        if job.status != cloud_status:
            job.status = cloud_status
            job.message = record.last_error
            await self.gctx.job_repository.update_job_status(job)  # type: ignore[union-attr]
        await self._execution_repository.update(
            job.job_id,
            record.state,
            cloud_status=cloud_status,
            last_error=record.last_error,
        )

    async def _reconcile_terminal_job(
        self,
        job: Job,
        record: ExecutionRecord,
    ) -> bool:
        if record.state is ExecutionState.RESULT_READY and job.status != "succeeded":
            return False
        if job.status == "cancelled":
            slurm_job_id = await self._resolve_slurm_job_id(record)
            status = await self._slurm_client.get_status(slurm_job_id)
            if status is None or status.state is SchedulerState.UNKNOWN:
                message = (
                    "cannot confirm SLURM allocation state while startup "
                    f"cancellation is pending: {slurm_job_id}"
                )
                raise RuntimeError(message)
            if status.state in {
                SchedulerState.PENDING,
                SchedulerState.RUNNING,
            }:
                await self._slurm_client.cancel(slurm_job_id)
                message = (
                    "startup cancellation is pending scheduler confirmation: "
                    f"{slurm_job_id}"
                )
                raise RuntimeError(message)
            if status.state is not SchedulerState.CANCELLED:
                message = (
                    "Cloud cancellation conflicts with SLURM terminal state "
                    f"{status.raw_state}: {slurm_job_id}"
                )
                raise RuntimeError(message)
            state = ExecutionState.CANCELLED
        elif job.status == "failed":
            state = ExecutionState.FAILED
        elif job.status == "succeeded":
            state = ExecutionState.SUCCEEDED
        else:
            return False
        await self._execution_repository.update(
            job.job_id,
            state,
            expected={record.state, state},
            cloud_status=job.status,
        )
        if state is ExecutionState.SUCCEEDED:
            await self._cleanup_succeeded_artifacts(job.job_id)
        return True

    async def _resolve_slurm_job_id(self, record: ExecutionRecord) -> str:
        if record.slurm_job_id is not None:
            return record.slurm_job_id
        if record.request_path is None:
            message = (
                "pending scheduler confirmation: SLURM request artifact is missing"
            )
            raise RuntimeError(message)
        if record.options is None:
            message = "pending scheduler confirmation: SLURM options are missing"
            raise RuntimeError(message)
        try:
            request_text = await asyncio.to_thread(
                Path(record.request_path).read_text,
                encoding="utf-8",
            )
            request = QulacsExecutionRequest.model_validate_json(request_text)
            options = SlurmSimulatorOptions.model_validate(record.options).resolve(
                n_qubits=request.n_qubits,
                qubits_per_node=self._qubits_per_node,
                max_nodes=self._max_nodes,
                max_n_per_node=self._max_n_per_node,
                max_timeout_seconds=self._max_timeout_seconds,
            )
        except Exception as exc:
            message = "cannot reconstruct persisted SLURM submit intent"
            raise RuntimeError(message) from exc
        digest = request_hash(request, options)
        job_name, _ = build_slurm_job_labels(record.cloud_job_id, digest)
        try:
            slurm_job_id = await self._slurm_client.find_job(
                job_name=job_name,
                start_time=(
                    datetime.fromisoformat(record.created_at) - timedelta(days=1)
                ),
            )
        except SlurmReconciliationAmbiguousError:
            raise
        except Exception as exc:
            message = "SLURM submission reconciliation is uncertain"
            raise RuntimeError(message) from exc
        if slurm_job_id is None:
            message = f"pending scheduler confirmation for SLURM JobName {job_name}"
            raise RuntimeError(message)
        return slurm_job_id

    async def _cleanup_succeeded_artifacts(self, job_id: str) -> None:
        if self._work_root is None:
            return
        try:
            await self._execution_repository.cleanup_artifacts(
                job_id,
                self._work_root,
            )
        except Exception:
            logger.exception(
                "failed to clean finalized SLURM artifacts",
                extra={"job_id": job_id},
            )

    async def _cleanup_expired_artifacts(self) -> None:
        if self._work_root is None:
            return
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=self._artifact_ttl_seconds)
        records = await self._execution_repository.list_cleanup_candidates(cutoff)
        for record in records:
            try:
                await self._execution_repository.cleanup_artifacts(
                    record.cloud_job_id,
                    self._work_root,
                )
            except Exception:
                logger.exception(
                    "failed to clean expired SLURM artifacts",
                    extra={"job_id": record.cloud_job_id},
                )

    async def poll_once(self) -> int:
        """Claim and enqueue one page each of submitted and stranded ready jobs."""
        gctx = self.gctx
        if gctx is None or gctx.job_repository is None or gctx.device is None:
            message = "SLURM job fetcher is not fully configured."
            raise RuntimeError(message)

        candidates: dict[str, Job] = {}
        for status in ("submitted", "ready"):
            jobs = await gctx.job_repository.get_jobs(
                device_id=gctx.device.device_id,
                status=status,
                limit=self._limit,
            )
            for job in jobs:
                candidates[job.job_id] = job

        claimed: list[Job] = []
        for job in candidates.values():
            if job.job_id in self._scheduled_job_ids:
                continue
            record = await self._execution_repository.get(job.job_id)
            if record is None or record.state is ExecutionState.READY:
                if not await self._execution_repository.claim(job.job_id, job.job_type):
                    continue
            elif record.state in {
                ExecutionState.CANCELLED,
                ExecutionState.FAILED,
                ExecutionState.SUCCEEDED,
            }:
                continue
            claimed.append(job)
        await self._enqueue(claimed)
        return len(claimed)

    async def _enqueue(self, jobs: list[Job]) -> None:
        pipeline = self.pipeline
        gctx = self.gctx
        if pipeline is None or gctx is None or not jobs:
            return

        pending: list[Job] = []
        for job in jobs:
            if job.job_id in self._scheduled_job_ids:
                continue
            unsupported_reason = self._unsupported_reason(job)
            if unsupported_reason is not None:
                job.status = "failed"
                job.message = unsupported_reason
                await gctx.job_repository.update_job_status(job)  # type: ignore[union-attr]
                await self._execution_repository.update(
                    job.job_id,
                    ExecutionState.FAILED,
                    cloud_status="failed",
                    last_error=unsupported_reason,
                )
                continue
            pending.append(job)
        for job in pending:
            self._scheduled_job_ids.add(job.job_id)
        recovered: list[Job] = []
        needs_download: list[Job] = []
        for job in pending:
            record = await self._execution_repository.get(job.job_id)
            request_exists = (
                record is not None
                and record.request_path is not None
                and await asyncio.to_thread(Path(record.request_path).is_file)
            )
            if request_exists:
                job.transpiler_info = {"transpiler_lib": None}
                recovered.append(job)
            else:
                needs_download.append(job)

        downloaded = await self._download_inputs(needs_download)
        ready = [*recovered, *downloaded]
        ready_ids = {job.job_id for job in ready}
        self._scheduled_job_ids.difference_update(
            job.job_id for job in pending if job.job_id not in ready_ids
        )
        for job in ready:
            await pipeline.execute_pipeline(gctx, JobContext(), job)

    @staticmethod
    def _unsupported_reason(job: Job) -> str | None:
        if job.job_type not in {"sampling", "estimation"}:
            return f"unsupported SLURM simulator job type: {job.job_type}"
        if any(bool(value) for value in job.mitigation_info.values()):
            return "mitigation is unsupported by the SLURM simulator PoC"
        return None
