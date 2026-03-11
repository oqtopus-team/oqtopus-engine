
import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .device_fetcher import DeviceFetcher
    from .job_fetcher import JobFetcher
    from .job_repository import JobRepository
    from .job_storage import JobStorage

from oqtopus_engine_core.utils.config_util import mask_sensitive_info
from oqtopus_engine_core.utils.di_container import DiContainer

from .context import GlobalContext
from .pipeline_builder import PipelineBuilder

logger = logging.getLogger(__name__)


class Engine:
    """Engine: The main entry point for running the OQTOPUS Engine application.

    The Engine is responsible for initializing the global context, building the
    pipeline, and starting the main components.
    The Engine is designed to be simple and focused on orchestration. It does not
    contain any business logic or component implementations.
    """

    def __init__(self, config: dict) -> None:
        """Initialize the Engine with the given configuration.

        Args:
            config: The configuration dictionary for the Engine. This should contain
                all necessary configuration for the global context, DI container,
                pipeline executor, and other components.

        """
        # Initialize the global context
        self._gctx = GlobalContext(config=config)
        logger.info("gctx.config=%s", mask_sensitive_info(self._gctx.config))

        # Initialize the DI container
        logger.error("di_container.config=%s", self._gctx.config["di_container"])
        self._dicon = DiContainer(**self._gctx.config["di_container"])

        # Build the pipeline executor using the PipelineBuilder
        self._pipeline = PipelineBuilder.build(
            self._gctx.config["pipeline_executor"],
            self._dicon
        )

    async def start(self) -> None:
        """Start the Engine application.

        This method initializes the main components and starts them concurrently.

        """
        # Initialize the job fetcher
        job_fetcher: JobFetcher = self._dicon.get("job_fetcher")
        job_fetcher.gctx = self._gctx
        job_fetcher.pipeline = self._pipeline

        # Initialize the job repository
        job_repository: JobRepository = self._dicon.get("job_repository")
        self._gctx.job_repository = job_repository

        # Initialize the job storage
        job_storage: JobStorage = self._dicon.get("job_storage")
        self._gctx.job_storage = job_storage

        # Initialize the device fetcher
        device_fetcher: DeviceFetcher = self._dicon.get("device_fetcher")
        device_fetcher.gctx = self._gctx

        # Initialize the device repository
        self._gctx.device_repository = self._dicon.get("device_repository")

        # Start components concurrently
        await self._run_components([
            self._pipeline,
            job_fetcher,
            device_fetcher,
        ])

        logger.info("OQTOPUS Engine started")

    @staticmethod
    async def _run_components(components: list) -> None:
        tasks = [asyncio.create_task(c.start()) for c in components]
        await asyncio.gather(*tasks)
