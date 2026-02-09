import logging

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext, Step

logger = logging.getLogger(__name__)


class DebugStep(Step):
    """Debug step that logs job and context info."""

    async def pre_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job by logging job and context information.

        This method logs the job and the job context.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        logger.debug(
            "debug dump",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job": job,
                "jctx": jctx,
            },
        )

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job by logging job and context information.

        This method logs the job and the job context.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        logger.debug(
            "debug dump",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job": job,
                "jctx": jctx,
            },
        )
