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

    def _log_noop(self, operation: str, **extra: object) -> None:
        """Log a no-op repository operation for debugging."""
        logger.debug(
            "NullJobRepository skipped %s",
            operation,
            extra={"repository": self.__class__.__name__, **extra},
        )

    async def get_jobs(
        self, device_id: str, status: str = "submitted", limit: int = 10
    ) -> list[Job]:
        """Return no jobs.

        Returns:
            An empty list because persistence is disabled.

        """
        self._log_noop(
            "get_jobs",
            device_id=device_id,
            status=status,
            limit=limit,
        )
        return []

    async def get_job_upload_url(
        self, job: Job, items: list[str]
    ) -> list[JobsJobInfoUploadPresignedURL]:
        """Return placeholder upload URLs.

        Returns:
            Placeholder upload URLs so callers can stay storage-agnostic.

        """
        self._log_noop("get_job_upload_url", job_id=job.job_id, items=items)
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
        """Return an empty input payload.

        Returns:
            An empty dictionary because persistence is disabled.

        """
        self._log_noop("download_job_input", job_id=job.job_id)
        return {}

    async def upload_job_output(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = "",
    ) -> None:
        """Log and discard the upload request."""
        self._log_noop(
            "upload_job_output",
            job_id=job.job_id,
            job_type=job.job_type,
            key=getattr(getattr(presigned_url, "fields", None), "key", None),
            arcname_ext=arcname_ext,
            data_type=type(data).__name__,
        )

    async def upload_job_output_nowait(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = "",
        *,
        preserve_order: bool = True,
    ) -> None:
        """Log and discard the asynchronous upload request."""
        self._log_noop(
            "upload_job_output_nowait",
            job_id=job.job_id,
            job_type=job.job_type,
            key=getattr(getattr(presigned_url, "fields", None), "key", None),
            arcname_ext=arcname_ext,
            preserve_order=preserve_order,
            data_type=type(data).__name__,
        )

    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """Log and discard the status update request."""
        self._log_noop(
            "update_job_status",
            job_id=job.job_id,
            execution_time=execution_time,
        )

    async def update_job_status_nowait(
        self,
        job: Job,
        execution_time: float | None = None,
        *,
        preserve_order: bool = True,
    ) -> None:
        """Log and discard the asynchronous status update request."""
        self._log_noop(
            "update_job_status_nowait",
            job_id=job.job_id,
            execution_time=execution_time,
            preserve_order=preserve_order,
        )

    async def update_job_transpiler_info(self, job: Job) -> None:
        """Log and discard the transpiler info update request."""
        self._log_noop("update_job_transpiler_info", job_id=job.job_id)

    async def update_job_transpiler_info_nowait(
        self,
        job: Job,
        *,
        preserve_order: bool = True,
    ) -> None:
        """Log and discard the asynchronous transpiler info update request."""
        self._log_noop(
            "update_job_transpiler_info_nowait",
            job_id=job.job_id,
            preserve_order=preserve_order,
        )
