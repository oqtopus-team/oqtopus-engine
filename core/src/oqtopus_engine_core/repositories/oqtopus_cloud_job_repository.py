import asyncio
import copy
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from oqtopus_engine_core.framework import Job, JobRepository
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    ApiClient,
    Configuration,
    JobsApi,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJobInfoUploadPresignedURL,
    JobsJobStatusUpdate,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.utils.storage_util import OqtopusStorage, OqtopusStorageError

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
        storage_op_timeout_seconds: int = 60,
        max_file_size: int = 10485760,
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

        # Per-job task chains to ensure sequential execution for the same job_id
        self._job_tails: dict[str, asyncio.Future] = {}
        self._job_tails_lock = asyncio.Lock()

        self._proxy = proxy
        self._storage_op_timeout_seconds = storage_op_timeout_seconds
        self._max_file_size = max_file_size

        logger.info(
            "OqtopusCloudJobRepository was initialized",
            extra={
                "url": url,
                "proxy": proxy,
                "workers": workers,
                "storage_op_timeout_seconds": storage_op_timeout_seconds,
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
            call: Callable that performs the HTTP request and returns
                (data, status, headers).
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

    async def _enqueue_and_run(
        self,
        job_id: str,
        coroutine: Coroutine[Any, Any, Any],
    ) -> None:
        """Schedule a coroutine for the given job_id while preserving execution order.

        This method ensures that all operations associated with the same ``job_id``
        are executed strictly in FIFO order, even when scheduled concurrently.

        The implementation uses a *per-job task chain* instead of an explicit queue
        or worker. For each ``job_id`` we keep a reference to the last scheduled
        task (the "tail"). A new task waits for the previous one before executing.

        Conceptually:

            job_id = X

                task A
                ↓
                task B waits A
                ↓
                task C waits B

        This guarantees that requests affecting the same job (for example
        status updates, job_info updates, or SSE log updates) are never sent
        concurrently and cannot overtake each other.

        Advantages of this design:

        - No background worker management
        - No queue lifecycle management
        - Strict per-job ordering
        - Minimal shared state
        - No race conditions around queue creation or deletion

        The last task reference is stored in ``_job_tails``. When the final task
        in the chain completes, the entry is removed to prevent memory growth.

        Args:
            job_id:
                Identifier of the job used as the ordering key.

            coroutine:
                The coroutine that performs the actual operation
                (typically an HTTP request).

        """
        async with self._job_tails_lock:
            previous = self._job_tails.get(job_id)

            async def runner() -> None:
                """Execute the coroutine after the previous task finishes."""
                if previous is not None:
                    try:
                        # Wait for the previous operation for this job.
                        # Errors from the previous task must not block
                        # subsequent operations.
                        await previous
                    except Exception:
                        # Log the exception to ensure traceability while
                        # allowing subsequent operations to proceed.
                        logger.exception(
                            "Previous task failed",
                            extra={"job_id": job_id},
                        )

                try:
                    # Execute the requested operation.
                    await coroutine
                except Exception:
                    logger.exception(
                        "job task failed",
                        extra={"job_id": job_id},
                    )

            # Create the new task and set it as the new tail.
            task = asyncio.create_task(runner())
            self._job_tails[job_id] = task

        try:
            # Wait for this task to complete.
            # This ensures proper propagation when called directly.
            await task
        finally:
            # Remove the tail entry if this task is still the latest one.
            # This prevents memory leaks when no further tasks are scheduled.
            async with self._job_tails_lock:
                if self._job_tails.get(job_id) is task:
                    self._job_tails.pop(job_id, None)

    async def _storage_request_with_error_logging(
        self,
        call: Callable[[], T],
        label: str,
        extra: dict[str, Any],
    ) -> T | None:
        """Call a storage request in a worker thread with logging and error handling."""
        async with self._sem:
            try:
                return await asyncio.to_thread(call)
            except OqtopusStorageError as ex:
                logger.info(
                    "%s: storage error",
                    label,
                    extra={
                        "error": str(ex),
                        **extra,
                    },
                )
                raise
            except Exception:
                logger.info(
                    "%s: unexpected error",
                    label,
                    extra=extra,
                )
                raise

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

    async def get_job_upload_url(
        self, job: Job, items: list[str]
    ) -> list[JobsJobInfoUploadPresignedURL]:
        """Fetch presigned URLs for job information items upload to Oqtopus Cloud storage.

        Args:
            job: The job to upload
            items: The list of job information items to upload. Available job information items are: `combined_program`, `transpile_result`, `result`, `sse_log`.

        Returns:
            A list of presigned URL data for upload, arranged in the order specified by the `items` parameter.

        """

        def _call() -> tuple[list[JobsJobInfoUploadPresignedURL], int, dict]:
            return self._jobs_api.get_upload_with_http_info(
                job_id=job.job_id, items=",".join(items)
            )

        extra: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "items": items,
        }
        logger.info(
            "GET /jobs/{job_id}/upload: request",
            extra={**extra},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "GET /jobs/{job_id}/upload",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "GET /jobs/{job_id}/upload: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "len(body)": len(response) if response is not None else 0,
            },
        )

        return response

    async def download_job_input(
        self,
        job: Job,
    ) -> dict[str, Any]:
        """Download and extract job input .zip file from cloud storage."""

        def _call() -> dict[str, Any]:
            proxies = (
                {"http": self._proxy, "https": self._proxy} if self._proxy else None
            )
            return OqtopusStorage.download(
                presigned_url=job.input,
                proxies=proxies,
                timeout_s=self._storage_op_timeout_seconds,
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
        response = await self._storage_request_with_error_logging(
            _call,
            "job input download",
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

    async def upload_job_output(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = "",
    ) -> None:
        """Upload job output data as a .zip file to cloud storage."""

        def _call() -> None:
            proxies = (
                {"http": self._proxy, "https": self._proxy} if self._proxy else None
            )
            return OqtopusStorage.upload(
                presigned_url=presigned_url,
                data=data,
                arcname_ext=arcname_ext,
                max_size=self._max_file_size,
                proxies=proxies,
                timeout_s=self._storage_op_timeout_seconds,
            )

        extra: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": job.job_type,
        }

        logger.info(
            "job output upload started",
            extra={
                **extra,
                "url": presigned_url.url,
                "key": presigned_url.fields.key,
            },
        )

        start = time.perf_counter()
        await self._storage_request_with_error_logging(
            _call,
            "job output upload",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "job output upload completed",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "url": presigned_url.url,
                "key": presigned_url.fields.key,
            },
        )

        job.output_files.append(presigned_url.fields.key)

    async def upload_job_output_nowait(
        self,
        job: Job,
        presigned_url: JobsJobInfoUploadPresignedURL,
        data: dict[str, Any] | str,
        arcname_ext: str = "",
        *,
        preserve_order: bool = True,
    ) -> None:
        """Upload job output data to cloud storage without waiting."""
        if preserve_order:
            task = asyncio.create_task(
                self._enqueue_and_run(
                    job.job_id,
                    self.upload_job_output(
                        job,
                        presigned_url,
                        data,
                        arcname_ext,
                    ),
                )
            )
        else:
            task = asyncio.create_task(
                self.upload_job_output(
                    job,
                    presigned_url,
                    data,
                    arcname_ext,
                )
            )

        self._track_background_request(
            task,
            label="job output upload",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )

    async def update_job_status(
        self,
        job: Job,
    ) -> None:
        """Send a PATCH request to update the job status and status related data and wait for the response.

        Args:
            job: The job to patch

        """
        body = JobsJobStatusUpdate(
            status=job.status,
            output_files=job.output_files,
            message=job.message,
            execution_time=job.execution_time,
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
        *,
        preserve_order: bool = True,
    ) -> None:
        """Send a PATCH request to update the job status and status related data without waiting.

        Args:
            job: The job to patch
            preserve_order:
                If ``True`` (default), operations targeting the same ``job_id``
                are executed sequentially so that updates cannot overtake each
                other. If ``False``, this ordering guarantee is disabled and the
                request may run concurrently with other updates for the same job.

        """
        # Take a shallow copy immediately to capture the current 'status'.
        # This prevents the value from changing while waiting in the queue.
        output_files_snapshot = copy.deepcopy(job.output_files)
        job_snapshot = job.model_copy(deep=False)
        job_snapshot.output_files = output_files_snapshot

        if preserve_order:
            task = asyncio.create_task(
                self._enqueue_and_run(job.job_id, self.update_job_status(job_snapshot))
            )
        else:
            task = asyncio.create_task(self.update_job_status(job_snapshot))

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
        # Use deepcopy for transpiler_info to ensure all nested structures
        # are preserved as they were at the moment of the call.
        body = copy.deepcopy(job.transpiler_info)

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
        *,
        preserve_order: bool = True,
    ) -> None:
        """Send a PUT request to update transpiler info without waiting.

        Args:
            job: The job to update.
            preserve_order:
                If ``True`` (default), operations targeting the same ``job_id``
                are executed sequentially so that updates cannot overtake each
                other. If ``False``, this ordering guarantee is disabled and the
                request may run concurrently with other updates for the same job.

        """
        # Take a shallow copy immediately to capture the current 'transpiler_info'.
        # This prevents the value from changing while waiting in the queue.
        transpiler_info_snapshot = copy.deepcopy(job.transpiler_info)
        job_snapshot = job.model_copy(deep=False)
        job_snapshot.transpiler_info = transpiler_info_snapshot

        if preserve_order:
            task = asyncio.create_task(
                self._enqueue_and_run(
                    job.job_id, self.update_job_transpiler_info(job_snapshot)
                )
            )
        else:
            task = asyncio.create_task(self.update_job_transpiler_info(job_snapshot))
        self._track_background_request(
            task,
            label="PUT /jobs/{job_id}/transpiler_info",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )
