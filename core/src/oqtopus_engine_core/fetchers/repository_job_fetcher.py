import asyncio
import logging

from oqtopus_engine_core.framework import JobContext, JobFetcher
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
                logger.exception("failed to fetch jobs")
                await asyncio.sleep(self._interval_seconds)
