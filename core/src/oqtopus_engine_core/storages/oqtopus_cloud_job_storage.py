import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from oqtopus_engine_core.framework import Job, JobStorage
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJobInfoUploadPresignedURL,
)
from oqtopus_engine_core.utils.storage_util import OqtopusStorage, OqtopusStorageError

logger = logging.getLogger(__name__)


class OqtopusCloudJobStorage(JobStorage):
    """Job storage implementation for Oqtopus Cloud."""

    T = TypeVar("T")

    def __init__(
        self,
        proxy: str | None = None,
        workers: int = 5,
        storage_op_timeout_seconds: int = 60,
    ) -> None:
        """Initialize the job storage.

        Args:
            proxy: The proxy URL for the storage request.
            workers: The number of concurrent workers to use for storage requests.
            storage_op_timeout_seconds: The timeout for storage operation in seconds.

        """
        super().__init__()

        self._sem = asyncio.Semaphore(workers)

        # Background request tasks:
        # Requests that are sent without waiting for the response
        self._background_requests: set[asyncio.Task[Any]] = set()

        self._proxy = proxy
        self._storage_op_timeout_seconds = storage_op_timeout_seconds

        logger.info(
            "OqtopusCloudJobsStorage was initialized",
            extra={
                "proxy": proxy,
                "workers": workers,
                "storage_op_timeout_seconds": storage_op_timeout_seconds
            },
        )

    async def _request_with_error_logging(
        self,
        call: Callable[[], T],
        label: str,
        extra: dict[str, Any],
    ) -> T | None:
        """Call a storage request in a worker thread with logging and error handling.

        Args:
            call: Callable that performs the HTTP request.
            label: Log label.
            extra: Extra fields to log on error.

        Returns:
            The data returned by the call, or None if an error occurred.

        Raises:
            OqtopusStorageError: If storage error occurs.

        """
        async with self._sem:
            try:
                return await asyncio.to_thread(call)
            except OqtopusStorageError as ex:
                # Note:
                # - Logged at INFO level because the caller performs the actual
                #   error handling at a higher layer
                # - This log is only a diagnostic breadcrumb, not a final failure record
                logger.info(
                    "%s: storage error",
                    label,
                    extra={
                        "error": str(ex),
                        **extra,
                    }
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

    async def download_job_input(
        self,
        job: Job,
    ) -> dict[str, Any]:
        """Downloads and extracts job input .zip file form OCTOPUS Cloud S3 storage

        Args:
            job: The job for input download.

        Returns:
            A dictionary containing downloaded and extracted job input items.

        """

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
        response = await self._request_with_error_logging(
            _call,
            f"job input download",
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
        data: dict[str, Any],
    ) -> None:
        """Uploads job output data as .zip file to OCTOPUS Cloud S3 storage

        Args:
            job: The job for output upload.
            presigned_url: Presigned URL for upload.
            data: Data to be uploaded.

        """

        def _call() -> None:
            proxies = (
                {"http": self._proxy, "https": self._proxy} if self._proxy else None
            )
            return OqtopusStorage.upload(
                presigned_url=presigned_url,
                data=data,
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
        await self._request_with_error_logging(
            _call,
            f"job output upload",
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
