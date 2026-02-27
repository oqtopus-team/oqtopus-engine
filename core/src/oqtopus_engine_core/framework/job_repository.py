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
    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """Update job status and status related data.

        Args:
            job: The job to update
            execution_time: The execution time

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
        execution_time: float | None = None,
    ) -> None:
        """Update job status and status related data.

        Args:
            job: The job to update
            execution_time: The execution time

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
