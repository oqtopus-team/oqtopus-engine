import asyncio
import logging

# ruff: noqa: DOC201, DOC501
from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    Step,
    StepResult,
)
from oqtopus_engine_core.slurm import (
    ExecutionState,
    SlurmExecutionRepository,
    SlurmJobReader,
)

logger = logging.getLogger(__name__)


class SlurmResultFinalizeStep(Step):
    """Durably upload a SLURM result before closing its execution record."""

    def __init__(
        self,
        execution_repository: SlurmExecutionRepository,
        job_reader: SlurmJobReader,
        work_root: str | None = None,
        retry_count: int = 2,
        retry_interval_seconds: float = 1.0,
    ) -> None:
        if retry_count < 0:
            message = "finalization retry_count must not be negative"
            raise ValueError(message)
        self._execution_repository = execution_repository
        self._job_reader = job_reader
        self._work_root = work_root
        self._retry_count = retry_count
        self._retry_interval_seconds = retry_interval_seconds

    async def pre_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,  # noqa: ARG002
        job: Job,  # noqa: ARG002
    ) -> StepResult:
        """Leave finalization for reverse pipeline traversal."""
        return StepResult()

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> StepResult:
        """Upload the validated result and synchronously finalize Cloud status."""
        if gctx.job_repository is None:
            message = "job repository is not configured"
            raise RuntimeError(message)
        if job.result is None:
            message = "job result is None"
            raise ValueError(message)

        for attempt in range(self._retry_count + 1):
            try:
                return await self._finalize_once(gctx, job)
            except Exception:
                if attempt >= self._retry_count:
                    raise
                logger.warning(
                    "SLURM result finalization failed and will retry",
                    extra={"job_id": job.job_id, "attempt": attempt + 1},
                    exc_info=True,
                )
                await asyncio.sleep(self._retry_interval_seconds)
        message = "unreachable SLURM finalization retry state"
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
            await self._finalize_execution(job.job_id)
            return StepResult()

        await gctx.job_repository.upload_job_outputs(
            job=job,
            outputs=[("result", job.result.model_dump(), ".json", None)],
        )
        job.status = "succeeded"
        await self._finalize_execution(
            job.job_id,
            output_files=job.output_files,
            message=job.message,
            execution_time=job.execution_time,
        )
        logger.info(
            "SLURM execution finalized",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )
        return StepResult()

    async def _finalize_execution(
        self,
        job_id: str,
        *,
        output_files: list[str] | None = None,
        message: str | None = None,
        execution_time: float | None = None,
    ) -> None:
        await self._execution_repository.update(
            job_id,
            ExecutionState.SUCCEEDED,
            expected={ExecutionState.RESULT_READY},
            cloud_status="succeeded",
            output_files=output_files,
            message=message,
            execution_time=execution_time,
        )
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
