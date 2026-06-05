import asyncio
import logging

from pydantic import ValidationError

from oqtopus_engine_core.framework import Job, JobContext, JobFetcher, JobInput
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

    async def start(self) -> None:
        """Start periodically fetching jobs and feeding them into the pipeline."""
        self.validate_fetcher_ready()
        gctx = self.gctx
        pipeline = self.pipeline
        if gctx is None or pipeline is None:  # pragma: no cover
            return

        logger.info("RepositoryJobFetcher was started")
        while True:
            try:
                await wait_until_fetchable(
                    gctx, pipeline, self._interval_seconds, self._job_fetch_threshold
                )

                jobs = await gctx.job_repository.get_jobs(  # type: ignore[union-attr]
                    device_id=gctx.device.device_id,  # type: ignore[union-attr]
                    status="submitted",
                    limit=self._limit,
                )
                logger.info(
                    "jobs fetched",
                    extra={"job_count": len(jobs)},
                )

                if len(jobs):
                    jobs = await self._download_inputs(jobs)
                    for job in jobs:
                        # Send job to pipeline
                        jctx = JobContext()
                        await pipeline.execute_pipeline(gctx, jctx, job)

                # Sleep if fewer jobs than the fetch limit were returned
                if len(jobs) < self._limit:
                    logger.debug(
                        "sleeping before next fetch",
                        extra={"sleep_seconds": self._interval_seconds},
                    )
                    await asyncio.sleep(self._interval_seconds)

            except Exception:
                logger.exception("unexpected error during job fetch")
                await asyncio.sleep(self._interval_seconds)

    async def _download_inputs(
        self,
        jobs: list[Job],
    ) -> list[Job]:
        """Download and validate job inputs.

        Returns:
            Jobs whose downloaded input payloads were validated successfully.

        """
        jobs_with_inputs = []

        try:
            # Create a list of awaitable tasks for downloading job inputs
            job_download_tasks = [
                self.gctx.job_repository.download_job_input(job)  # type: ignore[union-attr]
                for job in jobs
            ]
            # Run all download tasks concurrently
            job_download_results = await asyncio.gather(
                *job_download_tasks, return_exceptions=True
            )
            success_count = sum(
                1 for i in job_download_results if not isinstance(i, Exception)
            )
            logger.info(
                "jobs downloaded",
                extra={
                    "success": success_count,
                    "failed": len(jobs) - success_count,
                },
            )

            for i, job in enumerate(jobs):
                if not isinstance(job_download_results[i], Exception):
                    try:
                        validated_input = JobInput(**job_download_results[i])  # type: ignore[arg-type]
                        job.program = validated_input.program
                        job.operator = validated_input.operator
                        job.sse_program = validated_input.sse_program
                        jobs_with_inputs.append(job)
                    except ValidationError as exc:
                        await self._fail_job(
                            job, f"Job input validation failed: {exc!s}"
                        )
                else:
                    await self._fail_job(
                        job,
                        f"Job input download failed: {job_download_results[i]!s}",
                    )
        except Exception:
            logger.exception("unexpected error during job input download")

        return jobs_with_inputs

    async def _fail_job(
        self,
        job: Job,
        message: str,
    ) -> None:
        """Set the job as failed in the job repository."""
        try:
            job.status = "failed"
            job.message = message
            await self.gctx.job_repository.update_job_status(job=job)  # type: ignore[union-attr]
        except Exception:
            logger.exception(
                "failed to update job status to 'failed' in the repository",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
