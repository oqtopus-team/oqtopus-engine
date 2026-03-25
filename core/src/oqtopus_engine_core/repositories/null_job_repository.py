import logging
from typing import Any

from oqtopus_engine_core.framework import Job, JobRepository
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    JobsJobInfoUploadPresignedURL,
    JobsJobInfoUploadPresignedURLFields,
)

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
        return []

    async def get_job_upload_url(
        self, job: Job, items: list[str]
    ) -> list[JobsJobInfoUploadPresignedURL]:
        """Return placeholder upload URLs so callers can stay storage-agnostic."""
        return [
            JobsJobInfoUploadPresignedURL(
                url="null://upload",
                fields=JobsJobInfoUploadPresignedURLFields(
                    key=f"{job.job_id}/{item}",
                ),
            )
            for item in items
        ]

    async def download_job_input(
        self,
        job: Job,
    ) -> dict[str, Any]:
        """No-op implementation."""
        return {}

    async def upload_job_output(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = "",
    ) -> None:
        """No-op implementation."""
        logger.debug(
            "NullJobRepository skipped upload",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "key": getattr(getattr(presigned_url, "fields", None), "key", None),
                "arcname_ext": arcname_ext,
            },
        )
        return None

    async def upload_job_output_nowait(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = "",
    ) -> None:
        """No-op implementation."""
        logger.debug(
            "NullJobRepository skipped upload_nowait",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "key": getattr(getattr(presigned_url, "fields", None), "key", None),
                "arcname_ext": arcname_ext,
            },
        )
        return None

    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """No-op implementation."""
        return None

    async def update_job_status_nowait(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """No-op implementation."""
        return None

    async def update_job_transpiler_info(self, job: Job) -> None:
        """No-op implementation."""
        return None

    async def update_job_transpiler_info_nowait(self, job: Job) -> None:
        """No-op implementation."""
        return None
