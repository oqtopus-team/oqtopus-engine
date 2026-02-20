import asyncio
import logging
from abc import ABC, abstractmethod

from .context import GlobalContext
from .pipeline import PipelineExecutor

logger = logging.getLogger(__name__)


async def wait_until_fetchable(
    gctx: GlobalContext,
    pipeline: PipelineExecutor,
    interval_seconds: int,
    job_fetch_threshold: int,
) -> None:
    """Wait until the device is ready and the job buffer is below the fetch threshold.

    Args:
        gctx: The global context containing device information.
        pipeline: The pipeline executor.
        interval_seconds: The interval in seconds to wait before rechecking.
        job_fetch_threshold: The threshold of jobs in the buffer to trigger fetching.

    """
    while True:
        if gctx.device is None:
            logger.info(
                "gctx.device is None. sleeping before next fetch",
                extra={"sleep_seconds": interval_seconds},
            )
            await asyncio.sleep(interval_seconds)
            continue

        if gctx.device.is_connected is False:
            logger.info(
                "device is not connected, sleeping before next fetch",
                extra={
                    "sleep_seconds": interval_seconds,
                },
            )
            await asyncio.sleep(interval_seconds)
            continue

        if gctx.device.status != "active":
            logger.info(
                "device not active, sleeping before next fetch",
                extra={
                    "device_status": gctx.device.status,
                    "sleep_seconds": interval_seconds,
                },
            )
            await asyncio.sleep(interval_seconds)
            continue

        buffer_size = pipeline._job_buffer.size()
        if buffer_size >= job_fetch_threshold:
            logger.debug(
                "job buffer reached fetch threshold, sleeping before next fetch",
                extra={
                    "buffer_size": buffer_size,
                    "fetch_threshold": job_fetch_threshold,
                    "sleep_seconds": interval_seconds,
                },
            )
            await asyncio.sleep(interval_seconds)
            continue

        return


class JobFetcher(ABC):
    """Abstract base class for fetching jobs and feeding them into the pipeline."""

    def __init__(self) -> None:
        self._pipeline: PipelineExecutor | None = None
        self._gctx: GlobalContext | None = None

    @property
    def gctx(self) -> GlobalContext | None:
        """Get the current global context.

        Returns:
            The GlobalContext instance or None.

        """
        return self._gctx

    @gctx.setter
    def gctx(self, gctx: GlobalContext) -> None:
        """Set the global context shared across fetchers.

        Args:
            gctx: Shared GlobalContext.

        """
        self._gctx = gctx

    @property
    def pipeline(self) -> PipelineExecutor | None:
        """Get the currently set pipeline executor.

        Returns:
            The PipelineExecutor instance or None.

        """
        return self._pipeline

    @pipeline.setter
    def pipeline(self, pipeline: PipelineExecutor) -> None:
        """Set the pipeline executor to handle job processing.

        Args:
            pipeline: An instance of PipelineExecutor.

        """
        self._pipeline = pipeline

    @abstractmethod
    async def start(self) -> None:
        """Start the job fetching process. Must be implemented by subclasses.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`start` must be implemented in subclasses of JobFetcher."
        raise NotImplementedError(message)

    def validate_fetcher_ready(self) -> None:
        """Validate that the given JobFetcher is ready to be executed.

        This function checks whether all required dependencies of the fetcher
        (such as the pipeline executor and the global context) have been properly
        set before starting the fetch operation.

        Raises:
            RuntimeError: If the fetcher is not fully initialized or is in an
                invalid state for execution.

        """
        pipeline = self.pipeline
        if pipeline is None:
            message = "PipelineExecutor must be set before starting the fetcher."
            raise RuntimeError(message)
        gctx = self.gctx
        if gctx is None:
            message = "Global context must be set before starting the fetcher."
            raise RuntimeError(message)
