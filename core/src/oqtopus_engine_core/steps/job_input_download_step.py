import logging

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext, JobInput, Step

logger = logging.getLogger(__name__)


class JobInputDownloadStep(Step):
    """Step that downloads job input data from repository storage."""

    def __init__(
        self,
    ) -> None:
        logger.info(
            "JobInputDownloadStep was initialized",
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job.

        This method downloads and extracts job input data from repository storage.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        input = await gctx.job_repository.download_job_input(
            job,
        )

        # use pydantic for final validation
        validated_input = JobInput(**input)

        job.program = validated_input.program
        job.operator = validated_input.operator

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> None:
        """Post-process the job.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
