from abc import ABC, abstractmethod
from typing import Any

from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.interfaces.oqtopus_cloud import JobsJobInfoUploadPresignedURL

JobOutput = tuple[str, dict[str, Any] | str, str, str | None]


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
    async def get_job_upload_urls(
        self, job: Job, items: list[str]
    ) -> list[JobsJobInfoUploadPresignedURL]:
        """Fetch presigned URLs for job information item uploads.

        Args:
            job: The job to upload.
            items: Job information items to upload.
                Available values are `combined_program`, `transpile_result`,
                `result`, and `sse_log`.

        Returns:
            Presigned URL data for upload in the same order as `items`.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`get_job_upload_urls` must be implemented in subclasses of JobRepository."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def download_job_input(
        self,
        job: Job,
    ) -> dict[str, Any]:
        """Download and extract the job input payload.

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
    async def upload_job_outputs(
        self,
        job: Job,
        outputs: list[JobOutput],
    ) -> None:
        """Upload a group of job output files.

        Args:
            job: The job for output upload.
            outputs: Output items represented as
                ``(item, data, arcname_ext, arcname)``.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`upload_job_outputs` must be implemented in subclasses of JobRepository."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def upload_job_outputs_nowait(
        self,
        job: Job,
        outputs: list[JobOutput],
        *,
        preserve_order: bool = True,
    ) -> None:
        """Upload a group of job output files without waiting.

        Args:
            job: The job for output upload.
            outputs: Output items represented as
                ``(item, data, arcname_ext, arcname)``.
            preserve_order:
                If ``True`` (default), operations targeting the same ``job_id``
                are executed sequentially so that updates cannot overtake each
                other. If ``False``, this ordering guarantee is disabled and the
                request may run concurrently with other updates for the same job.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`upload_job_outputs_nowait` must be implemented in subclasses of "
            "JobRepository."
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
        *,
        preserve_order: bool = True,
    ) -> None:
        """Update job status and status related data.

        Args:
            job: The job to update
            preserve_order:
                If ``True`` (default), operations targeting the same ``job_id``
                are executed sequentially so that updates cannot overtake each
                other. If ``False``, this ordering guarantee is disabled and the
                request may run concurrently with other updates for the same job.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_job_status_nowait` must be implemented in subclasses of "
            "JobRepository."
        )
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
    async def update_job_transpiler_info_nowait(
        self, job: Job, *, preserve_order: bool = True
    ) -> None:
        """Update transpiler info without waiting.

        Args:
            job: The job to update
            preserve_order:
                If ``True`` (default), operations targeting the same ``job_id``
                are executed sequentially so that updates cannot overtake each
                other. If ``False``, this ordering guarantee is disabled and the
                request may run concurrently with other updates for the same job.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_job_transpiler_info_nowait` must be implemented in "
            "subclasses of "
            "JobUpdater."
        )
        raise NotImplementedError(message)
