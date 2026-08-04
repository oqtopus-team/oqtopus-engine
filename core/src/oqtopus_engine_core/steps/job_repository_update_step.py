import logging

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    Step,
    StepResult,
)

logger = logging.getLogger(__name__)


class JobRepositoryUpdateStep(Step):
    """Step that updates the job on job repository."""

    def __init__(
        self,
    ) -> None:
        logger.info(
            "JobRepositoryUpdateStep was initialized",
        )

    @staticmethod
    def _get_sse_log_file_name(gctx: GlobalContext) -> str | None:
        registry = gctx.config.get("di_container", {}).get("registry", {})
        sse_step = registry.get("sse_step", {})
        runner_settings = sse_step.get("runner_settings", {})
        return runner_settings.get("log_file_name")

    async def pre_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,  # noqa: ARG002
        job: Job,  # noqa: ARG002
    ) -> StepResult:
        """Pre-process the job.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        return StepResult()

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> StepResult:
        """Post-process the job by updating its status in the job repository.

        This method updates the job's status and execution time n the job repository.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            ValueError: If the job result or SSE log is missing.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        items = ["result"]
        if job.job_type == "sse":
            items.append("sse_log")
        urls = await gctx.job_repository.get_job_upload_url(  # type: ignore[union-attr]
            job=job,
            items=items,
        )

        if job.result is None:
            message = "job result is None"
            raise ValueError(message)
        await gctx.job_repository.upload_job_output(  # type: ignore[union-attr]
            job=job,
            presigned_url=urls[0],
            data=job.result.model_dump(),
            arcname_ext=".json",
        )

        if job.job_type == "sse":
            if job.sse_log is None:
                message = "job sse_log is None"
                raise ValueError(message)
            await gctx.job_repository.upload_job_output(  # type: ignore[union-attr]
                job=job,
                presigned_url=urls[1],
                data=job.sse_log,
                arcname_ext=".log",
                arcname=self._get_sse_log_file_name(gctx),
            )

        job.status = "succeeded"
        await gctx.job_repository.update_job_status_nowait(job)  # type: ignore[union-attr]
        return StepResult()
