import logging

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    PipelineExceptionHandler,
)

logger = logging.getLogger(__name__)


class FailJobRepositoryHandler(PipelineExceptionHandler):
    """Abstract base class for handling exceptions in the pipeline."""

    async def handle_exception(
        self,
        ex: Exception,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Handle an exception raised during pipeline execution."""
        if jctx.get("has_actual_children", False):
            # if there are child jobs, update their status
            await self._update_jobs_status(ex, gctx, job.children)
        else:
            await self._update_jobs_status(ex, gctx, [job])

    @staticmethod
    async def _update_jobs_status(
        ex: Exception,
        gctx: GlobalContext,
        jobs: list[Job]
    ) -> None:
        """Update the job status to "failed" for the given jobs."""
        for job in jobs:
            try:
                job.status = "failed"
                job.message = str(ex)
                await gctx.job_repository.update_job_status(
                    job=job,
                )
            except Exception:
                logger.exception(
                    "failed to update job status to 'failed' in the repository",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
