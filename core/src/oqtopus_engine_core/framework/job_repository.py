from abc import ABC, abstractmethod

from .model import Job


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
    async def update_job_status(self, job: Job) -> None:
        """Update job status.

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
    async def update_job_status_nowait(self, job: Job) -> None:
        """Update job status without waiting.

        Args:
            job: The job to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`update_job_status_nowait` must be implemented in subclasses of JobRepository."
        raise NotImplementedError(message)

    @abstractmethod
    async def update_job_info(
        self,
        job: Job,
        overwrite_status: str | None = None,
        execution_time: float | None = None,
    ) -> None:
        """Update job info.

        Args:
            job: The job to update
            overwrite_status: The status to overwrite in the job info if not None.
            execution_time: The execution time to overwrite in the job info if not None.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_job_info` must be implemented in subclasses of JobRepository."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def update_job_info_nowait(
        self,
        job: Job,
        overwrite_status: str | None = None,
        execution_time: float | None = None,
    ) -> None:
        """Update job info.

        Args:
            job: The job to update
            overwrite_status: The status to overwrite in the job info if not None.
            execution_time: The execution time to overwrite in the job info if not None.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`update_job_info_nowait` must be implemented in subclasses of JobRepository."
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
    async def get_ssesrc(self, job_id: str) -> str:
        """Get SSE source URL for job updates.

        Args:
            job_id: The job ID.

        Returns:
            The SSE source URL as a string.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`get_ssesrc` must be implemented in subclasses of JobRepository."
        raise NotImplementedError(message)

    @abstractmethod
    async def update_sselog(self, job_id: str, sselog: str) -> None:
        """Update SSE log.

        Args:
            job_id: The job ID.
            sselog: The SSE log to update.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`update_sselog` must be implemented in subclasses of JobRepository."
        raise NotImplementedError(message)

    @abstractmethod
    async def update_sselog_nowait(self, job_id: str, sselog: str) -> None:
        """Update SSE log without waiting.

        Args:
            job_id: The job ID.
            sselog: The SSE log to update.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_sselog_nowait` must be implemented in subclasses of JobRepository."
        )
        raise NotImplementedError(message)
