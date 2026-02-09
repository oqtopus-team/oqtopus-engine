import asyncio
import logging

from oqtopus_engine_core.framework import Job, JobContext, JobFetcher, JobInfo
from oqtopus_engine_core.framework.job_fetcher import wait_until_fetchable
from oqtopus_engine_core.framework.model import OperatorItem

logger = logging.getLogger(__name__)


program = """OPENQASM 3;
include "stdgates.inc";
qubit[2] q;
bit[2] c;

h q[0];
cx q[0], q[1];
c = measure q;
"""


class MockJobFetcher(JobFetcher):
    """Fetch jobs from an Oqtopus Cloud HTTP API and feed them into the pipeline."""

    def __init__(
        self,
        interval_seconds: float = 5.0,
        limit: int = 10,
        job_fetch_threshold: int = 10,
    ) -> None:
        """Initialize the mock job fetcher.

        Args:
            interval_seconds: Interval in seconds between each job fetch.
            limit: Maximum number of jobs to fetch per request.
            job_fetch_threshold: The threshold of jobs in the buffer to trigger fetching.

        """
        super().__init__()
        self._interval_seconds = interval_seconds
        self._limit = limit
        self._job_fetch_threshold = job_fetch_threshold
        logger.info(
            "MockJobFetcher was initialized",
            extra={
                "interval_seconds": self._interval_seconds,
                "limit": self._limit,
                "job_fetch_threshold": self._job_fetch_threshold,
            },
        )

    async def start(self) -> None:
        """Periodically fetch jobs and feed them into the pipeline."""
        self.validate_fetcher_ready()
        gctx = self.gctx
        pipeline = self.pipeline

        logger.info("MockJobFetcher was started")
        count = 0
        while True:
            try:
                await wait_until_fetchable(
                    gctx, pipeline, self._interval_seconds, self._job_fetch_threshold
                )

                count += 1
                jobs: list[Job] = []
                for index in range(self._limit):
                    # sampling
                    job = Job(
                        job_id=f"{count}-{index}",
                        device_id="qulacs",
                        shots=1000,
                        job_type="sampling",
                        job_info=JobInfo(program=[program]),
                        transpiler_info={
                            "transpiler_lib": "qiskit",
                            "transpiler_options": {"optimization_level": 2},
                        },
                        simulator_info={},
                        mitigation_info={},
                        status="ready",
                    )
                    jobs.append(job)

                    # mitigation
                    job = Job(
                        job_id=f"{count}-{index}",
                        device_id="qulacs",
                        shots=1000,
                        job_type="sampling",
                        job_info=JobInfo(program=[program]),
                        transpiler_info={},
                        simulator_info={},
                        mitigation_info={
                            "ro_error_mitigation": "pseudo_inverse",
                        },
                        status="ready",
                    )
                    jobs.append(job)

                    # estimation
                    job = Job(
                        job_id=f"{count}-{index}",
                        device_id="qulacs",
                        shots=1000,
                        job_type="estimation",
                        job_info=JobInfo(
                            program=[program],
                            operator=[
                                OperatorItem(pauli="X0 X1", coeff=1.0),
                                OperatorItem(pauli="Z0 Z1", coeff=1.0),
                            ],
                        ),
                        transpiler_info={},
                        simulator_info={},
                        mitigation_info={},
                        status="ready",
                    )
                    jobs.append(job)

                    # multi_manual
                    job = Job(
                        job_id=f"{count}-{index}",
                        device_id="qulacs",
                        shots=1000,
                        job_type="multi_manual",
                        job_info=JobInfo(program=[program, program]),
                        transpiler_info={},
                        simulator_info={},
                        mitigation_info={},
                        status="ready",
                    )
                    jobs.append(job)

                for job in jobs:
                    logger.debug(
                        "fetched job details: %s",
                        job,
                        extra={
                            "job_id": job.job_id,
                            "job_type": job.job_type,
                        },
                    )
                    jctx = JobContext()
                    await pipeline.execute_pipeline(gctx, jctx, job)

            except Exception:
                logger.exception("failed to fetch jobs")

            logger.debug(
                "sleeping before next fetch",
                extra={"sleep_seconds": self._interval_seconds},
            )
            await asyncio.sleep(self._interval_seconds)
