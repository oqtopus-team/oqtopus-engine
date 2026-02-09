import logging

from oqtopus_engine_core.framework import Job, JobRepository

logger = logging.getLogger(__name__)


class NullJobRepository(JobRepository):
    """Null-object implementation of JobRepository.

    This repository intentionally performs no operations and does not
    interact with any external system. It is safe to use in environments
    where job persistence is disabled.
    """

    def __init__(self) -> None:
        """Initialize the job repository."""
        super().__init__()

        logger.info(
            "NullJobRepository was initialized",
        )

    async def get_jobs(
        self, device_id: str, status: str = "submitted", limit: int = 10
    ) -> list[Job]:
        """No-op implementation."""

    async def update_job_status(self, job: Job) -> None:
        """No-op implementation."""

    async def update_job_status_nowait(self, job: Job) -> None:
        """No-op implementation."""

    async def update_job_info(
        self,
        job: Job,
        overwrite_status: str | None = None,
        execution_time: float | None = None,
    ) -> None:
        """No-op implementation."""

    async def update_job_info_nowait(
        self,
        job: Job,
        overwrite_status: str | None = None,
        execution_time: float | None = None,
    ) -> None:
        """No-op implementation."""

    async def update_job_transpiler_info(self, job: Job) -> None:
        """No-op implementation."""

    async def update_job_transpiler_info_nowait(self, job: Job) -> None:
        """No-op implementation."""

    async def get_ssesrc(self, job_id: str) -> str:
        """No-op implementation."""

    async def update_sselog(self, job_id: str, sselog: str) -> None:
        """No-op implementation."""

    async def update_sselog_nowait(self, job_id: str, sselog: str) -> None:
        """No-op implementation."""
