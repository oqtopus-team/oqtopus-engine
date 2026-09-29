import logging
from pathlib import Path

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobRepository,
    PipelineExceptionHandler,
)
from oqtopus_engine_core.simulator.execution import (
    ExecutionRepository,
    ExecutionState,
    JobReader,
    LocalExecutionRepository,
)
from oqtopus_engine_core.simulator.scheduler import SchedulerState
from oqtopus_engine_core.simulator.scheduler.slurm import (
    SlurmClient,
    SlurmSubmissionUncertainError,
)
from oqtopus_engine_core.steps.slurm_simulator_step import (
    JobCancelledError,
    SlurmCancellationPendingError,
)

logger = logging.getLogger(__name__)


class SlurmPipelineExceptionHandler(PipelineExceptionHandler):
    """Reconcile pipeline failures without overwriting user cancellation."""

    def __init__(
        self,
        execution_repository: ExecutionRepository,
        job_reader: JobReader,
        work_root: str,
        slurm_client: SlurmClient,
    ) -> None:
        self._execution_repository = execution_repository
        self._job_reader = job_reader
        self._work_root = Path(work_root).expanduser()
        self._internal_execution_repository = LocalExecutionRepository(work_root)
        self._slurm_client = slurm_client

    async def handle_exception(
        self,
        ex: Exception,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> None:
        """Persist terminal status and diagnostics on the Cloud job."""
        if gctx.job_repository is None:
            logger.error(
                "job repository is unavailable while handling SLURM failure",
                extra={"job_id": job.job_id},
            )
            return

        if job.parent is not None:
            await self._handle_internal_child_failure(ex, gctx, job)
            return

        await self._handle_root_failure(ex, gctx.job_repository, job)
        await self._cleanup_terminal_children(job)

    async def _handle_root_failure(  # noqa: PLR0911
        self, ex: Exception, job_repository: JobRepository, job: Job
    ) -> None:
        cloud_job = await self._job_reader.get_job(job.job_id)
        cancelled = isinstance(ex, JobCancelledError) or (
            cloud_job is not None and cloud_job.status == "cancelled"
        )
        record = await self._execution_repository.get(job.job_id)
        if isinstance(ex, SlurmSubmissionUncertainError):
            logger.error(
                "preserving uncertain SLURM submission for startup reconciliation",
                extra={"job_id": job.job_id},
            )
            return
        if (
            isinstance(ex, SlurmCancellationPendingError)
            and record is not None
            and record.state is ExecutionState.RUNNING
        ):
            logger.warning(
                "SLURM cancellation remains pending for startup retry",
                extra={"job_id": job.job_id},
            )
            return
        if cancelled:
            if record is not None and record.state not in {
                ExecutionState.RESULT_READY,
                ExecutionState.SUCCEEDED,
            }:
                await self._execution_repository.update(
                    job.job_id,
                    ExecutionState.CANCELLED,
                    expected={record.state},
                    cloud_status="cancelled",
                    last_error=str(ex),
                    message=str(ex),
                )
            elif record is not None and record.state is not ExecutionState.SUCCEEDED:
                logger.warning(
                    "SLURM result checkpoint takes precedence over cancellation",
                    extra={"job_id": job.job_id},
                )
                return
            logger.info(
                "SLURM cancellation is synchronized with Cloud",
                extra={"job_id": job.job_id},
            )
            return

        if record is not None and record.state is ExecutionState.RESULT_READY:
            await self._execution_repository.update(
                job.job_id,
                ExecutionState.RESULT_READY,
                last_error=str(ex),
            )
            logger.error(
                "preserving validated SLURM result for startup retry",
                extra={"job_id": job.job_id},
            )
            return

        if record is not None and record.state is ExecutionState.CANCELLED:
            await self._execution_repository.update(
                job.job_id,
                ExecutionState.CANCELLED,
                cloud_status="cancelled",
                last_error=str(ex),
                message=str(ex),
            )
            return

        if record is not None and record.state is ExecutionState.FAILED:
            await self._execution_repository.update(
                job.job_id,
                record.state,
                cloud_status="failed",
                last_error=str(ex),
                message=str(ex),
            )
            return

        if record is not None and record.state not in {
            ExecutionState.CANCELLED,
            ExecutionState.FAILED,
            ExecutionState.SUCCEEDED,
        }:
            await self._execution_repository.update(
                job.job_id,
                ExecutionState.FAILED,
                cloud_status="failed",
                last_error=str(ex),
                message=str(ex),
            )
            return
        job.status = "failed"
        job.message = str(ex)
        await job_repository.update_job_status(job)

    async def _handle_internal_child_failure(
        self,
        ex: Exception,
        gctx: GlobalContext,
        job: Job,
    ) -> None:
        parent = job.parent
        if parent is None or gctx.job_repository is None:  # pragma: no cover
            return
        if isinstance(
            ex, (SlurmSubmissionUncertainError, SlurmCancellationPendingError)
        ):
            logger.warning(
                "preserving unresolved internal SLURM execution",
                extra={"job_id": job.job_id, "parent_job_id": parent.job_id},
            )
            return
        cloud_job = await self._job_reader.get_job(parent.job_id)
        record = await self._execution_repository.get(parent.job_id)
        cancelled = isinstance(ex, JobCancelledError) or (
            cloud_job is not None and cloud_job.status == "cancelled"
        )
        target_state = ExecutionState.CANCELLED if cancelled else ExecutionState.FAILED
        target_status = "cancelled" if cancelled else "failed"
        if record is not None and record.state not in {
            ExecutionState.RESULT_READY,
            ExecutionState.SUCCEEDED,
            ExecutionState.CANCELLED,
            ExecutionState.FAILED,
        }:
            await self._execution_repository.update(
                parent.job_id,
                target_state,
                expected={record.state},
                cloud_status=target_status,
                last_error=str(ex),
                message=str(ex),
            )
        elif record is None:
            parent.status = target_status
            parent.message = str(ex)
            await gctx.job_repository.update_job_status(parent)
        logger.error(
            "internal SLURM child failure was applied to the root job",
            extra={
                "job_id": job.job_id,
                "parent_job_id": parent.job_id,
                "status": target_status,
            },
        )
        await self._cleanup_terminal_children(parent, failed_child=job, error=ex)

    async def _cleanup_terminal_children(
        self,
        parent: Job,
        *,
        failed_child: Job | None = None,
        error: Exception | None = None,
    ) -> None:
        try:
            root = await self._execution_repository.get(parent.job_id)
            if root is None or root.state not in {
                ExecutionState.FAILED,
                ExecutionState.CANCELLED,
                ExecutionState.SUCCEEDED,
            }:
                return
        except Exception:
            logger.exception(
                "failed to check root status for child cleanup",
                extra={"job_id": parent.job_id},
            )
            return
        children = {child.job_id: child for child in parent.children}
        if failed_child is not None:
            children[failed_child.job_id] = failed_child
        for child in children.values():
            try:
                if child is failed_child:
                    await self._close_failed_child(child, error)
                record = await self._internal_execution_repository.get(child.job_id)
                if record is not None and record.state in {
                    ExecutionState.FAILED,
                    ExecutionState.CANCELLED,
                    ExecutionState.SUCCEEDED,
                }:
                    await self._internal_execution_repository.cleanup_artifacts(
                        child.job_id, self._work_root
                    )
            except Exception:
                logger.exception(
                    "failed to clean terminal SLURM child",
                    extra={"job_id": child.job_id, "parent_job_id": parent.job_id},
                )

    async def _close_failed_child(self, child: Job, error: Exception | None) -> None:
        repository = self._internal_execution_repository
        record = await repository.get(child.job_id)
        if record is None or record.state in {
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.SUCCEEDED,
        }:
            return
        state = (
            ExecutionState.CANCELLED
            if isinstance(error, JobCancelledError)
            else ExecutionState.FAILED
        )
        if record.state is ExecutionState.RUNNING:
            # An exception alone does not prove the allocation has stopped.
            if record.slurm_job_id is None:
                return
            status = await self._slurm_client.get_status(record.slurm_job_id)
            if status is None or status.state not in {
                SchedulerState.COMPLETED,
                SchedulerState.CANCELLED,
                SchedulerState.FAILED,
            }:
                return
            if status.state is SchedulerState.CANCELLED:
                state = ExecutionState.CANCELLED
        await repository.update(
            child.job_id, state, expected={record.state}, last_error=str(error)
        )
