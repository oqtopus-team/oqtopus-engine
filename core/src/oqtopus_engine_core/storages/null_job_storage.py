import logging
from typing import Any

from oqtopus_engine_core.framework import Job, JobStorage
from oqtopus_engine_core.interfaces.oqtopus_cloud import JobsJobInfoUploadPresignedURL

logger = logging.getLogger(__name__)


class NullJobStorage(JobStorage):
    """Null-object implementation of JobStorage.

    This storage intentionally performs no operations and does not
    interact with any external system. It is safe to use in environments
    where job persistence is disabled.
    """

    def __init__(self) -> None:
        """Initialize the job storage."""
        super().__init__()

        logger.info(
            "NullJobStorage was initialized",
        )

    async def download_job_input(
        self,
        job: Job,
    ) -> dict[str, Any]:
        """No-op implementation."""

    async def upload_job_output(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = ""
    ) -> None:
        """No-op implementation."""

    async def upload_job_output_nowait(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = ""
    ) -> None:
        """No-op implementation."""