import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from oqtopus_engine_core.framework import Job, JobRepository
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    ApiClient,
    Configuration,
    JobApi,
    JobsApi,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJobStatusUpdate,
    JobsUpdateJobInfoRequest,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException

logger = logging.getLogger(__name__)


class OqtopusCloudJobRepository(JobRepository):
    """Job repository implementation for Oqtopus Cloud."""

    T = TypeVar("T")

    def __init__(
        self,
        url: str = "http://localhost:8888",
        api_key: str = "",
        proxy: str | None = None,
        workers: int = 5,  # noqa: ARG002
    ) -> None:
        """Initialize the job repository with the API URL and interval.

        Args:
            url: The endpoint URL to fetch jobs from.
            api_key: The API key for authentication.
            proxy: The proxy URL for the API request.
            workers: Kept for backward compatibility; no longer used.

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
        self._job_api = JobApi(api_client=api_client)  # for sse
        self._jobs_api = JobsApi(api_client=api_client)

        # Background request tasks:
        # Requests that are sent without waiting for the response
        self._background_requests: set[asyncio.Task[Any]] = set()

        # Per-job queues for ordering nowait requests
        self._job_queues: dict[str, asyncio.Queue[Coroutine[None, None, None]]] = {}
        # Tracks job_ids whose queue currently has an active drainer task
        self._job_draining: set[str] = set()
        self._job_queues_lock = asyncio.Lock()

        logger.info(
            "OqtopusCloudJobRepository was initialized",
            extra={
                "url": url,
                "proxy": proxy,
            },
        )

    async def _request_with_error_logging(  # noqa: PLR6301
        self,
        call: Callable[[], T],
        label: str,
        extra: dict[str, Any],
    ) -> T | None:
        """Call an API in a worker thread with logging and error handling.

        Args:
            call: Callable that performs the HTTP request and returns
                (data, status, headers).
            label: Log label like 'PATCH /jobs/{job_id}/job_info'.
            extra: Extra fields to log on error.

        Returns:
            The data returned by the call, or None if an error occurred.

        Raises:
            ApiException: If an API error occurs.

        """
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
        self, job_id: str, coro: Coroutine[None, None, None]
    ) -> None:
        """Enqueue a coroutine for the given job_id and run it sequentially.

        Ensures that requests for the same job_id are executed in order by
        designating exactly one task per job_id as the drainer. Other tasks
        that enqueue for the same job_id return immediately after enqueuing,
        leaving the active drainer to process the item. The queue and its
        drainer marker are removed when the queue becomes empty.

        Args:
            job_id: The job identifier used to determine the queue.
            coro: The coroutine to enqueue and execute.

        """
        async with self._job_queues_lock:
            if job_id not in self._job_queues:
                self._job_queues[job_id] = asyncio.Queue()
            queue = self._job_queues[job_id]
            queue.put_nowait(coro)
            # Become the drainer only if no drainer task is currently active
            is_drainer = job_id not in self._job_draining
            if is_drainer:
                self._job_draining.add(job_id)

        if not is_drainer:
            # An active drainer will pick up the enqueued item; nothing more to do
            return

        # This task is the designated drainer for job_id
        while True:
            async with self._job_queues_lock:
                if queue.empty():
                    # Remove the queue and drainer marker when fully drained
                    if self._job_queues.get(job_id) is queue:
                        del self._job_queues[job_id]
                    self._job_draining.discard(job_id)
                    break
                current_coro = queue.get_nowait()

            await current_coro

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

    async def update_job_status(self, job: Job) -> None:
        """Send a PATCH request to update the job status and wait for the response.

        Args:
            job: The job whose status will be updated

        """
        body = JobsJobStatusUpdate(status=job.status)

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
            extra={
                **extra,
                "body": body,
            },
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
        self, job: Job, *, use_job_queue: bool = True
    ) -> None:
        """Send a PATCH request to update the job status without waiting.

        Args:
            job: The job whose status will be updated.
            use_job_queue: If True (default), the request is queued per job_id
                to guarantee ordering. If False, the request is executed immediately
                without queuing (for priority processing).

        """
        if use_job_queue:
            task = asyncio.create_task(
                self._enqueue_and_run(job.job_id, self.update_job_status(job))
            )
        else:
            task = asyncio.create_task(self.update_job_status(job))
        self._track_background_request(
            task,
            label="PATCH /jobs/{job_id}/status",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )

    async def update_job_info(
        self,
        job: Job,
        overwrite_status: str | None = None,
        execution_time: float | None = None,
    ) -> None:
        """Send a PATCH request update job info and wait for the response.

        Args:
            job: The job to patch
            overwrite_status: The status to overwrite in the job info if not None.
            execution_time: The execution time to overwrite in the job info if not None.

        """
        job_info = {
            "combined_program": job.job_info.combined_program,
            "transpile_result": (
                job.job_info.transpile_result.model_dump(exclude_none=True)
                if job.job_info.transpile_result is not None
                else None
            ),
            "result": (
                job.job_info.result.model_dump(exclude_none=True)
                if job.job_info.result is not None
                else None
            ),
            "message": job.job_info.message,
        }
        body = JobsUpdateJobInfoRequest(
            overwrite_status=overwrite_status,
            execution_time=execution_time,
            job_info=job_info,
        )

        def _call() -> tuple[object, int, dict]:
            return self._jobs_api.patch_job_info_with_http_info(
                job_id=job.job_id,
                body=body,
            )

        extra: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": job.job_type,
        }

        logger.info(
            "PATCH /jobs/{job_id}/job_info: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "PATCH /jobs/{job_id}/job_info",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "PATCH /jobs/{job_id}/job_info: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": response,
            },
        )

    async def update_job_info_nowait(
        self,
        job: Job,
        overwrite_status: str | None = None,
        execution_time: float | None = None,
        *,
        use_job_queue: bool = True,
    ) -> None:
        """Send a PATCH request update job info without waiting.

        Args:
            job: The job to patch.
            overwrite_status: The status to overwrite in the job info if not None.
            execution_time: The execution time to overwrite in the job info if not None.
            use_job_queue: If True (default), the request is queued per job_id
                to guarantee ordering. If False, the request is executed immediately
                without queuing (for priority processing).

        """
        if use_job_queue:
            task = asyncio.create_task(
                self._enqueue_and_run(
                    job.job_id,
                    self.update_job_info(
                        job,
                        overwrite_status=overwrite_status,
                        execution_time=execution_time,
                    ),
                )
            )
        else:
            task = asyncio.create_task(
                self.update_job_info(
                    job,
                    overwrite_status=overwrite_status,
                    execution_time=execution_time,
                )
            )
        self._track_background_request(
            task,
            label="PATCH /jobs/{job_id}/job_info",
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
        *,
        use_job_queue: bool = True,
    ) -> None:
        """Send a PUT request to update transpiler info without waiting.

        Args:
            job: The job to update.
            use_job_queue: If True (default), the request is queued per job_id
                to guarantee ordering. If False, the request is executed immediately
                without queuing (for priority processing).

        """
        if use_job_queue:
            task = asyncio.create_task(
                self._enqueue_and_run(job.job_id, self.update_job_transpiler_info(job))
            )
        else:
            task = asyncio.create_task(self.update_job_transpiler_info(job))
        self._track_background_request(
            task,
            label="PUT /jobs/{job_id}/transpiler_info",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )

    async def get_ssesrc(self, job_id: str) -> str:
        """GET SSE program source file from Oqtopus Cloud.

        Args:
            job_id: Job identifier.

        Returns:
            SSE program source as string.

        """

        def _call() -> tuple[str, int, dict]:
            return self._job_api.get_ssesrc_with_http_info(job_id=job_id)

        extra: dict[str, Any] = {
            "job_id": job_id,
        }

        logger.info(
            "GET /jobs/{job_id}/ssesrc: request",
            extra={**extra},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "GET /jobs/{job_id}/ssesrc",  # noqa: RUF027
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "GET /jobs/{job_id}/ssesrc: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "len(body)": len(response) if response is not None else 0,
            },
        )

        return response

    async def update_sselog(self, job_id: str, sselog: str) -> None:
        """Send a PATCH request to update SSE log file and wait for the response.

        Args:
            job_id: Job identifier.
            sselog: SSE log content as string.

        """

        def _call() -> tuple[object, int, dict]:
            return self._job_api.patch_sselog_with_http_info(
                job_id=job_id,
                file=sselog,
            )

        extra: dict[str, Any] = {
            "job_id": job_id,
        }

        logger.info(
            "PATCH /jobs/{job_id}/sselog: request",
            extra={
                **extra,
                "len(body)": len(sselog) if sselog is not None else 0,
            },
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "PATCH /jobs/{job_id}/sselog",  # noqa: RUF027
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "PATCH /jobs/{job_id}/sselog: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": response,
            },
        )

    async def update_sselog_nowait(
        self, job_id: str, sselog: str, *, use_job_queue: bool = True
    ) -> None:
        """Send a PATCH request to update SSE log file without waiting.

        Args:
            job_id: Job identifier.
            sselog: SSE log content as string.
            use_job_queue: If True (default), the request is queued per job_id
                to guarantee ordering. If False, the request is executed immediately
                without queuing (for priority processing).

        """
        if use_job_queue:
            task = asyncio.create_task(
                self._enqueue_and_run(job_id, self.update_sselog(job_id, sselog))
            )
        else:
            task = asyncio.create_task(self.update_sselog(job_id, sselog))
        self._track_background_request(
            task,
            label="PATCH /jobs/{job_id}/sselog",  # noqa: RUF027
            extra={
                "job_id": job_id,
                "len(body)": len(sselog) if sselog is not None else 0,
            },
        )
