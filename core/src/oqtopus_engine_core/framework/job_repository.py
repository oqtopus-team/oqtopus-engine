from abc import ABC, abstractmethod
from typing import Any

from .model import Job
from ..interfaces.oqtopus_cloud import JobsJobInfoUploadPresignedURL


class JobRepository(ABC):
    """Abstract base class for job repository implementations."""

    @abstractmethod
    async def get_jobs(
        self, device_id: str, status: str = "ready", limit: int = 10
    ) -> list[Job]:
        """Fetch jobs from Oqtopus Cloud.

        Args:
            device_id: The device ID to filter jobs.
            status: The job status to filter jobs.
            limit: The maximum number of jobs to fetch.

        Returns:
            A list of jobs.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`get_jobs` must be implemented in subclasses of JobRepository."
        raise NotImplementedError(message)

    @abstractmethod
    async def get_job_upload_url(
        self, job: Job, items: list[str]
    ) -> list[JobsJobInfoUploadPresignedURL]:
        """Fetch presigned URLs for job information items upload to Oqtopus Cloud storage.

        Args:
            job: The job to upload
            items: The list of job information items to upload. Available job information items are: `combined_program`, `transpile_result`, `result`, `sse_log`.

        Returns:
            A list of presigned URL data for upload, arranged in the order specified by the `items` parameter.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`get_job_upload_url` must be implemented in subclasses of JobRepository."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def update_job_status(
        self,
        job: Job,
    ) -> None:
        """Update job status and status related data.

        Args:
            job: The job to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_job_status` must be implemented in subclasses of JobRepository."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def update_job_status_nowait(
        self,
        job: Job,
    ) -> None:
        """Update job status and status related data.

        Args:
            job: The job to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`update_job_status_nowait` must be implemented in subclasses of JobRepository."
        raise NotImplementedError(message)

    @abstractmethod
    async def update_job_transpiler_info(self, job: Job) -> None:
        """Update transpiler info.

        Args:
            job: The job to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_job_transpiler_info` must be implemented in subclasses of "
            "JobUpdater."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def update_job_transpiler_info_nowait(self, job: Job) -> None:
        """Update transpiler info without waiting.

        Args:
            job: The job to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_job_transpiler_info_nowait` must be implemented in subclasses of "
            "JobUpdater."
        )
        raise NotImplementedError(message)

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
            "`download_job_input` must be implemented in subclasses of JobRepository."
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
            "`upload_job_output` must be implemented in subclasses of JobRepository."
        )
        raise NotImplementedError(message)
