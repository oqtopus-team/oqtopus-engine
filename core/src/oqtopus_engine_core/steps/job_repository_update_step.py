import logging

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext, Step

logger = logging.getLogger(__name__)


class JobRepositoryUpdateStep(Step):
    """Step that updates the job on job repository."""

    def __init__(
        self,
    ) -> None:
        logger.info(
            "JobRepositoryUpdateStep was initialized",
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> None:
        """Post-process the job by updating its status in the job repository.

        This method updates the job's status and execution time n the job repository.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        items = ["result"]
        if job.job_type == "sse":
            items.append("sse_log")
        urls = await gctx.job_repository.get_job_upload_url(
            job=job,
            items=items,
        )

        if (job.result is None):
            message = "job result is None"
            raise ValueError(message)
        await gctx.job_storage.upload_job_output(
            job=job,
            presigned_url=urls[0],
            data=job.result.model_dump(),
            arcname_ext=".json"
        )

        if job.job_type == "sse":
            if (job.sse_log is None):
                message = "job sse_log is None"
                raise ValueError(message)
            await gctx.job_storage.upload_job_output(
                job=job,
                presigned_url=urls[1],
                data=job.sse_log,
                arcname_ext=".log"
            )

        job.status = "succeeded"
        await gctx.job_repository.update_job_status_nowait(job)
