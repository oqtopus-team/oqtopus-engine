from abc import ABC, abstractmethod
from typing import Any

from .model import Job
from ..interfaces.oqtopus_cloud import JobsJobInfoUploadPresignedURL


class JobStorage(ABC):
    """Abstract base class for job storage implementations."""

    @abstractmethod
    async def download_job_input(
        self,
        job: Job,
    ) -> dict[str, Any]:
        """Downloads and extracts job input .zip file form job data storage

        Args:
            job: The job for input download.

        Returns:
            A dictionary containing downloaded and extracted job input items.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`download_job_input` must be implemented in subclasses of JobStorage."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def upload_job_output(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any],
    ) -> None:
        """Uploads job output data as .zip file to job data storage

        Args:
            job: The job for output upload.
            presigned_url: Presigned URL for upload.
            data: Data to be uploaded.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`upload_job_output` must be implemented in subclasses of JobStorage."
        )
        raise NotImplementedError(message)
