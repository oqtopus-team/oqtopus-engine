import asyncio
import logging
from pathlib import Path

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    Step,
    StepResult,
)
from oqtopus_engine_core.slurm import (
    ExecutionRecord,
    ExecutionRepository,
    ExecutionState,
    JobReader,
    LocalExecutionRepository,
)

from .slurm_simulator_step import JobCancelledError

logger = logging.getLogger(__name__)

# ruff: noqa: DOC201, DOC501


class SessionStep(Step):
    """Open and close the root job session around pipeline execution."""

    def __init__(
        self,
        execution_repository: ExecutionRepository,
        job_reader: JobReader,
        work_root: str,
        finalize_retry_count: int = 2,
        finalize_retry_interval_seconds: float = 1.0,
    ) -> None:
        if finalize_retry_count < 0:
            message = "finalization retry_count must not be negative"
            raise ValueError(message)
        self._execution_repository = execution_repository
        self._internal_execution_repository = LocalExecutionRepository(work_root)
        self._job_reader = job_reader
        self._work_root = Path(work_root)
        self._finalize_retry_count = finalize_retry_count
        self._finalize_retry_interval_seconds = finalize_retry_interval_seconds

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> StepResult:
        """Initialize a root before downstream steps or an estimator split.

        Returns:
            A NONE directive so the pipeline continues to the estimator.

        """
        if job.parent is not None or job.job_type not in {"sampling", "estimation"}:
            return StepResult()
        if gctx.job_repository is None:
            message = "job repository is not configured"
            raise RuntimeError(message)

        await self._execution_repository.initialize()
        record = await self._execution_repository.get(job.job_id)
        if record is None:
            await self._execution_repository.claim(job.job_id, job.job_type)
            record = await self._execution_repository.get(job.job_id)
        if record is None:  # pragma: no cover
            message = f"failed to claim execution: {job.job_id}"
            raise RuntimeError(message)

        cloud_job = await self._job_reader.get_job(job.job_id)
        if cloud_job is not None and cloud_job.status in {"cancelled", "cancelling"}:
            await self._execution_repository.update(
                job.job_id,
                ExecutionState.CANCELLED,
                expected={record.state},
                cloud_status="cancelled",
            )
            message = "job was cancelled before SLURM submission"
            raise JobCancelledError(message)
        if record.state is ExecutionState.READY:
            await self._start_cloud_execution(gctx, job, cloud_job)
        return StepResult()

    async def _start_cloud_execution(
        self,
        gctx: GlobalContext,
        job: Job,
        cloud_job: Job | None,
    ) -> ExecutionRecord:
        if gctx.job_repository is None:  # pragma: no cover
            message = "job repository is not configured"
            raise RuntimeError(message)
        if cloud_job is not None and cloud_job.status == "submitted":
            job.status = "ready"
            await gctx.job_repository.update_job_status(job)
            await self._execution_repository.update(
                job.job_id,
                ExecutionState.READY,
                expected={ExecutionState.READY},
                cloud_status="ready",
            )
        if cloud_job is None or cloud_job.status != "running":
            job.status = "running"
            try:
                await gctx.job_repository.update_job_status(job)
            except Exception as exc:
                reconciled_cloud_job = await self._job_reader.get_job(job.job_id)
                if reconciled_cloud_job is not None and reconciled_cloud_job.status in {
                    "cancelled",
                    "cancelling",
                }:
                    await self._execution_repository.update(
                        job.job_id,
                        ExecutionState.CANCELLED,
                        expected={ExecutionState.READY},
                        cloud_status="cancelled",
                    )
                    message = "job was cancelled before SLURM submission"
                    raise JobCancelledError(message) from exc
                if (
                    reconciled_cloud_job is None
                    or reconciled_cloud_job.status != "running"
                ):
                    raise
                cloud_job = reconciled_cloud_job
        else:
            job.status = "running"
        current = await self._execution_repository.get(job.job_id)
        if current is not None and current.state is ExecutionState.RUNNING:
            return current
        return await self._execution_repository.update(
            job.job_id,
            ExecutionState.RUNNING,
            expected={ExecutionState.READY},
            cloud_status=(
                cloud_job.status
                if cloud_job is not None
                and cloud_job.status in {"running", "cancelling"}
                else "running"
            ),
        )

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> StepResult:
        """Upload and finalize a root result after all child jobs joined."""
        if job.parent is not None:
            return StepResult()
        if job.result is None:
            return StepResult()
        if gctx.job_repository is None:
            message = "job repository is not configured"
            raise RuntimeError(message)

        for attempt in range(self._finalize_retry_count + 1):
            try:
                return await self._finalize_once(gctx, job)
            except Exception:
                if attempt >= self._finalize_retry_count:
                    raise
                logger.warning(
                    "split-result finalization failed and will retry",
                    extra={"job_id": job.job_id, "attempt": attempt + 1},
                    exc_info=True,
                )
                await asyncio.sleep(self._finalize_retry_interval_seconds)
        message = "unreachable SLURM split-result finalization retry state"
        raise AssertionError(message)

    async def _finalize_once(
        self,
        gctx: GlobalContext,
        job: Job,
    ) -> StepResult:
        if gctx.job_repository is None or job.result is None:  # pragma: no cover
            message = "finalization prerequisites changed during retry"
            raise RuntimeError(message)
        cloud_job = await self._job_reader.get_job(job.job_id)
        if cloud_job is not None and cloud_job.status == "succeeded":
            await self._cleanup_root_artifacts(job.job_id)
            await self._cleanup_child_artifacts(job)
            return StepResult()

        record = await self._execution_repository.get(job.job_id)
        if record is None:
            message = f"execution record is missing: {job.job_id}"
            raise RuntimeError(message)
        if record.state is ExecutionState.SUCCEEDED:
            await self._cleanup_root_artifacts(job.job_id)
            await self._cleanup_child_artifacts(job)
            return StepResult()
        if record.state not in {ExecutionState.RUNNING, ExecutionState.RESULT_READY}:
            message = f"unexpected execution state for finalization: {record.state}"
            raise RuntimeError(message)

        await gctx.job_repository.upload_job_outputs(
            job=job,
            outputs=[("result", job.result.model_dump(), ".json", None)],
        )
        job.status = "succeeded"
        await self._execution_repository.update(
            job.job_id,
            ExecutionState.SUCCEEDED,
            expected={record.state},
            cloud_status="succeeded",
            output_files=job.output_files,
            message=job.message,
            execution_time=job.execution_time,
        )
        await self._cleanup_root_artifacts(job.job_id)
        await self._cleanup_child_artifacts(job)
        return StepResult()

    async def _cleanup_root_artifacts(self, job_id: str) -> None:
        try:
            await self._execution_repository.cleanup_artifacts(
                job_id,
                self._work_root,
            )
        except Exception:
            logger.exception(
                "failed to clean finalized split-result artifacts",
                extra={"job_id": job_id},
            )

    async def _cleanup_child_artifacts(self, job: Job) -> None:
        for child in job.children:
            try:
                await self._internal_execution_repository.cleanup_artifacts(
                    child.job_id,
                    self._work_root,
                )
            except KeyError:
                continue
            except Exception:
                logger.exception(
                    "failed to clean finalized scheduler child artifacts",
                    extra={
                        "job_id": job.job_id,
                        "child_job_id": child.job_id,
                    },
                )
