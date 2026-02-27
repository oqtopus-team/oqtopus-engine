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

    async def handle_exception(  # noqa: PLR6301
        self,
        ex: Exception,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> None:
        """Handle an exception raised during pipeline execution."""
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
