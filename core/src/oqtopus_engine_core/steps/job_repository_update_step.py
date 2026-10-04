import logging
from http import HTTPStatus

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobOutput,
    Step,
    StepResult,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException

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
            RuntimeError: If no job repository is configured.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        if job.result is None:
            message = "job result is None"
            raise ValueError(message)
        if gctx.job_repository is None:
            message = "job repository is not configured"
            raise RuntimeError(message)
        job_repository = gctx.job_repository

        outputs: list[JobOutput] = [("result", job.result.model_dump(), ".json", None)]
        if job.job_type == "sse":
            if job.sse_log is None:
                message = "job sse_log is None"
                raise ValueError(message)
            outputs.append((
                "sse_log",
                job.sse_log,
                ".log",
                self._get_sse_log_file_name(gctx),
            ))

        await job_repository.upload_job_outputs(
            job=job,
            outputs=outputs,
        )

        job.status = "succeeded"
        try:
            await job_repository.update_job_status_ordered(job)
        except Exception as e:
            logger.exception(
                "failed to report succeeded status; falling back to failed",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            job.status = "failed"
            job.message = f"engine could not report succeeded status: {e}"
            try:
                await job_repository.update_job_status_ordered(
                    job, include_output_files=False
                )
            except ApiException as fallback_ex:
                if fallback_ex.status == HTTPStatus.CONFLICT:
                    # The job already reached a terminal state through
                    # another path (e.g. cancelled by the user). Not an
                    # engine-side failure, so this is not raised to ERROR.
                    logger.info(
                        "fallback to failed status rejected; job already terminal",
                        extra={"job_id": job.job_id, "job_type": job.job_type},
                    )
                else:
                    logger.exception(
                        "failed to fall back to failed status",
                        extra={"job_id": job.job_id, "job_type": job.job_type},
                    )
            except Exception:
                logger.exception(
                    "failed to fall back to failed status",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
        return StepResult()
