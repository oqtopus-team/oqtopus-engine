import logging

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    PipelineExceptionHandler,
    resolve_repository_jobs,
)

logger = logging.getLogger(__name__)


class FailJobRepositoryHandler(PipelineExceptionHandler):
    """Abstract base class for handling exceptions in the pipeline."""

    async def handle_exception(
        self,
        ex: Exception,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> None:
        """Handle an exception raised during pipeline execution."""
        # `job` itself has no Cloud entity of its own when it is (or is a
        # descendant of) an MP-auto-combined job; the failure is not
        # specific to any single original job, so say so rather than
        # reporting one original job's exception message on all of them
        # (and never another job's job_id, since unrelated users' jobs may
        # be combined together).
        is_combined = job.repository_job_id is None
        await self._update_jobs_status(
            ex, gctx, resolve_repository_jobs(job), is_combined=is_combined
        )

    @staticmethod
    async def _update_jobs_status(
        ex: Exception,
        gctx: GlobalContext,
        jobs: list[Job],
        *,
        is_combined: bool = False,
    ) -> None:
        """Update the job status to "failed" for the given jobs."""
        message = (
            f"combined execution failed (multi-programming): {ex}"
            if is_combined
            else str(ex)
        )
        for job in jobs:
            try:
                job.status = "failed"
                job.message = message
                await gctx.job_repository.update_job_status(  # type: ignore[union-attr]
                    job=job,
                )
            except Exception:
                logger.exception(
                    "failed to update job status to 'failed' in the repository",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
