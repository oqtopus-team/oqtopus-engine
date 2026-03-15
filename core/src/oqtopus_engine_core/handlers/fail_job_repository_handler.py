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
        if hasattr(jctx, "mp_auto_combining"):
            # if mp auto combining, update status of child jobs
            await self._update_jobs_status(ex, gctx, job.children)
        else:
            await self._update_jobs_status(ex, gctx, job)

    @staticmethod
    async def _update_jobs_status(
        ex: Exception,
        gctx: GlobalContext,
        jobs: Job | list[Job]
    ) -> None:
        """Update the job status to "failed" for the given jobs."""
        if isinstance(jobs, Job):
            jobs = [jobs]

        for job in jobs:
            try:
                job.status = "failed"
                job.job_info.result = None  # Clear any partial results
                job.job_info.message = str(ex)
                await gctx.job_repository.update_job_info(
                    job=job,
                    overwrite_status=job.status,
                )
            except Exception:
                logger.exception(
                    "failed to update job status to 'failed' in the repository",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
