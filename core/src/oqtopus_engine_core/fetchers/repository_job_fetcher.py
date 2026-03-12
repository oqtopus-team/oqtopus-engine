import asyncio
import logging
from pydantic import ValidationError

from oqtopus_engine_core.framework import Job, JobContext, JobInput, JobFetcher
from oqtopus_engine_core.framework.job_fetcher import wait_until_fetchable

logger = logging.getLogger(__name__)


class RepositoryJobFetcher(JobFetcher):
    """Job fetcher that retrieves jobs from a job repository."""

    def __init__(
        self,
        interval_seconds: float = 5.0,
        limit: int = 10,
        job_fetch_threshold: int = 10,
    ) -> None:
        """Initialize the RepositoryJobFetcher.

        Args:
            interval_seconds: Interval in seconds between each job fetch.
            limit: Maximum number of jobs to fetch per request.
            job_fetch_threshold: Threshold of jobs in the buffer to trigger fetching.

        """
        super().__init__()
        self._interval_seconds = interval_seconds
        self._limit = limit
        self._job_fetch_threshold = job_fetch_threshold

        logger.info(
            "RepositoryJobFetcher was initialized",
            extra={
                "interval_seconds": self._interval_seconds,
                "limit": self._limit,
                "job_fetch_threshold": self._job_fetch_threshold,
            },
        )

    async def _fail_job(
        self,
        job: Job,
        message: str,
    ) -> None:
        try:
            job.status = "failed"
            job.message = message
            await self.gctx.job_repository.update_job_status(job=job)
        except Exception:
            logger.exception(
                "failed to update job status to 'failed' in the repository",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )

    async def start(self) -> None:
        """Start periodically fetching jobs and feeding them into the pipeline."""
        self.validate_fetcher_ready()
        gctx = self.gctx
        pipeline = self.pipeline

        logger.info("RepositoryJobFetcher was started")
        while True:
            try:
                await wait_until_fetchable(
                    gctx, pipeline, self._interval_seconds, self._job_fetch_threshold
                )

                jobs = await gctx.job_repository.get_jobs(
                    device_id=gctx.device.device_id,
                    status="submitted",
                    limit=self._limit,
                )
                logger.info(
                    "jobs fetched",
                    extra={"job_count": len(jobs)},
                )

                if len(jobs):
                    # Create a list of awaitable tasks for downloading job inputs
                    job_download_tasks = [
                        gctx.job_storage.download_job_input(job) for job in jobs
                    ]
                    # Run all download tasks concurrently
                    job_inputs = await asyncio.gather(
                        *job_download_tasks, return_exceptions=True
                    )
                    success_count = sum(
                        1 for i in job_inputs if not isinstance(i, Exception)
                    )
                    logger.info(
                        "jobs downloaded",
                        extra={
                            "success": success_count,
                            "failed": len(jobs) - success_count,
                        },
                    )

                    for i, job in enumerate(jobs):
                        if not isinstance(job_inputs[i], Exception):
                            try:
                                validated_input = JobInput(**job_inputs[i])
                                job.program = validated_input.program
                                job.operator = validated_input.operator
                                job.sse_program = validated_input.sse_program

                                # Send job to pipeline
                                jctx = JobContext()
                                await pipeline.execute_pipeline(gctx, jctx, job)

                            except ValidationError as exc:
                                await self._fail_job(
                                    job, f"Job input validation failed: {str(exc)}"
                                )
                        else:
                            await self._fail_job(
                                job, f"Job input download failed: {str(job_inputs[i])}"
                            )

                # Sleep if fewer jobs than the fetch limit were returned
                if len(jobs) < self._limit:
                    logger.debug(
                        "sleeping before next fetch",
                        extra={"sleep_seconds": self._interval_seconds},
                    )
                    await asyncio.sleep(self._interval_seconds)

            except Exception:
                logger.exception("failed to fetch jobs")
                await asyncio.sleep(self._interval_seconds)
