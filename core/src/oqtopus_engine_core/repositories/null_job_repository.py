import logging
from typing import Any

from oqtopus_engine_core.framework import Job, JobRepository
from oqtopus_engine_core.interfaces.oqtopus_cloud import JobsJobInfoUploadPresignedURL

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

    async def get_job_upload_url(
        self, job: Job, items: list[str]
    ) -> list[JobsJobInfoUploadPresignedURL]:
        """No-op implementation."""

    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """No-op implementation."""

    async def update_job_status_nowait(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """No-op implementation."""

    async def update_job_transpiler_info(self, job: Job) -> None:
        """No-op implementation."""

    async def update_job_transpiler_info_nowait(self, job: Job) -> None:
        """No-op implementation."""
