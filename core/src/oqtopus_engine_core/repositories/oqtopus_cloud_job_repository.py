import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from oqtopus_engine_core.framework import Job, JobRepository
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    ApiClient,
    Configuration,
    JobsApi,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJobStatusUpdate,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.utils.storage_util import OqtopusStorage

logger = logging.getLogger(__name__)


class OqtopusCloudJobRepository(JobRepository):
    """Job repository implementation for Oqtopus Cloud."""

    T = TypeVar("T")

    def __init__(
        self,
        url: str = "http://localhost:8888",
        api_key: str = "",
        proxy: str | None = None,
        workers: int = 5,
    ) -> None:
        """Initialize the job repository with the API URL and interval.

        Args:
            url: The endpoint URL to fetch jobs from.
            api_key: The API key for authentication.
            proxy: The proxy URL for the API request.
            workers: The number of concurrent workers to use for API requests.

        """
        super().__init__()
        # Construct JobsApi
        rest_config = Configuration()
        rest_config.host = url
        if proxy:
            rest_config.proxy = proxy
        api_client = ApiClient(
            configuration=rest_config,
            header_name="x-api-key",
            header_value=api_key,
        )
        self._jobs_api = JobsApi(api_client=api_client)
        self._sem = asyncio.Semaphore(workers)

        # Background request tasks:
        # Requests that are sent without waiting for the response
        self._background_requests: set[asyncio.Task[Any]] = set()

        logger.info(
            "OqtopusCloudJobRepository was initialized",
            extra={
                "url": url,
                "proxy": proxy,
                "workers": workers,
            },
        )

    async def _request_with_error_logging(
        self,
        call: Callable[[], T],
        label: str,
        extra: dict[str, Any],
    ) -> T | None:
        """Call an API in a worker thread with logging and error handling.

        Args:
            call: Callable that performs the HTTP request and returns (data, status, headers).
            label: Log label like 'PATCH /jobs/{job_id}/status'.
            extra: Extra fields to log on error.

        Returns:
            The data returned by the call, or None if an error occurred.

        Raises:
            ApiException: If an API error occurs.

        """
        async with self._sem:
            try:
                return await asyncio.to_thread(call)
            except ApiException as ex:
                # Note:
                # - Logged at INFO level because the caller performs the actual
                #   error handling at a higher layer
                # - This log is only a diagnostic breadcrumb, not a final failure record
                logger.info(
                    "%s: response",
                    label,
                    extra={
                        "status_code": ex.status,
                        "reason": ex.reason,
                        "body": str(ex.body),
                        **extra,
                    },
                )
                raise
            except Exception:
                # Same reasoning as above: avoid duplicate ERROR-level logs.
                logger.info(
                    "%s: unexpected error",
                    label,
                    extra=extra,
                )
                raise

    def _track_background_request(
        self,
        task: asyncio.Task[Any],
        label: str,
        extra: dict[str, Any],
    ) -> None:
        """Track a background request task and log its result.

        This method keeps a strong reference to the task so that:
        - the request continues running in the background, and
        - any exception raised during execution is logged.

        The caller does NOT wait for the request to finish.
        """
        self._background_requests.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._background_requests.discard(t)
            try:
                t.result()
            except asyncio.CancelledError:
                logger.info("%s: cancelled", label, extra=extra)
            except Exception:
                logger.exception("%s: failed", label, extra=extra)

        task.add_done_callback(_done)

    async def get_jobs(
        self, device_id: str, status: str = "submitted", limit: int = 10
    ) -> list[Job]:
        """Fetch jobs from Oqtopus Cloud.

        Args:
            device_id: The device ID to filter jobs.
            status: The job status to filter jobs.
            limit: The maximum number of jobs to fetch.

        Returns:
            A list of jobs.

        """

        def _call() -> tuple[object, int, dict]:
            return self._jobs_api.get_jobs_with_http_info(
                device_id=device_id,
                status=status,
                limit=limit,
            )

        extra: dict[str, Any] = {
            "device_id": device_id,
            "status": status,
            "limit": limit,
        }
        logger.info(
            "GET /jobs: request",
            extra={**extra},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "GET /jobs",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "GET /jobs: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                "len(body)": len(response) if response is not None else 0,
            },
        )

        jobs: list[Job] = []
        for job_oas in response:
            job = Job(**job_oas.to_dict())  # type: ignore[call-arg]
            jobs.append(job)
        return jobs

    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """Send a PATCH request to update the job status and status related data and wait for the response.

        Args:
            job: The job to patch
            execution_time: The execution time

        """
        body = JobsJobStatusUpdate(
            status=job.status,
            # TODO: add output_files & message
            output_files=None,
            message=None,
            execution_time=execution_time,
        )

        def _call() -> tuple[object, int, dict]:
            return self._jobs_api.patch_job_with_http_info(
                job_id=job.job_id,
                body=body,
            )

        extra: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": job.job_type,
        }

        logger.info(
            "PATCH /jobs/{job_id}/status: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "PATCH /jobs/{job_id}/status",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "PATCH /jobs/{job_id}/status: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": response,
            },
        )

    async def update_job_status_nowait(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        """Send a PATCH request to update the job status and status related data without waiting.

        Args:
            job: The job to patch
            execution_time: The execution time

        """
        task = asyncio.create_task(
            self.update_job_status(
                job,
                execution_time=execution_time,
            )
        )
        self._track_background_request(
            task,
            label="PATCH /jobs/{job_id}/status",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )

    async def update_job_transpiler_info(
        self,
        job: Job,
    ) -> None:
        """Send a PUT request to update transpiler info and wait for the response.

        Args:
            job: The job to update.

        """
        body = job.transpiler_info

        def _call() -> tuple[object, int, dict]:
            return self._jobs_api.update_job_transpiler_info_with_http_info(
                job_id=job.job_id,
                body=body,
            )

        extra: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": job.job_type,
        }

        logger.info(
            "PUT /jobs/{job_id}/transpiler_info: request",
            extra={
                **extra,
                "body": body,
            },
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "PUT /jobs/{job_id}/transpiler_info",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "PUT /jobs/{job_id}/transpiler_info: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": response,
            },
        )

    async def update_job_transpiler_info_nowait(
        self,
        job: Job,
    ) -> None:
        """Send a PUT request to update transpiler info without waiting.

        Args:
            job: The job to update.

        """
        task = asyncio.create_task(self.update_job_transpiler_info(job))
        self._track_background_request(
            task,
            label="PUT /jobs/{job_id}/transpiler_info",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )

    async def download_job_input(
        self,
        job: Job,
    ) -> dict[str, Any]:
        """Downloads and extracts job input .zip file form OCTOPUS Cloud S3 storage

        Args:
            job: The job for input download.

        """
        def _call() -> dict[str, Any]:
            return OqtopusStorage.download(
                presigned_url=job.input
            )

        extra: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": job.job_type,
        }

        logger.info(
            "job input download started",
            extra={
                **extra,
                "presigned_url": job.input,
            },
        )

        start = time.perf_counter()
        response = await self._request_with_error_logging(
            _call,
            f"job: {job.job_id} input download",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "job input download completed",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "presigned_url": job.input,
            },
        )

        return response
