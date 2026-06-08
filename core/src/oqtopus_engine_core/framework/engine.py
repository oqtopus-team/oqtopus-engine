
import asyncio
import logging
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .device_fetcher import DeviceFetcher
    from .job_fetcher import JobFetcher
    from .job_repository import JobRepository

from oqtopus_util.config import mask_sensitive_info
from oqtopus_util.di import DiContainer

from .context import GlobalContext
from .observability import instrument_clients, register_span_processor
from .pipeline_builder import PipelineBuilder

logger = logging.getLogger(__name__)

GRPC_OPTION_TARGETS = {
    "oqtopus_engine_core.fetchers.DeviceGatewayFetcher",
    "oqtopus_engine_core.fetchers.SseEngineGateway",
    "oqtopus_engine_core.mp.auto_combining.MpAutoCombiningBuffer",
    "oqtopus_engine_core.steps.DeviceGatewayStep",
    "oqtopus_engine_core.steps.EstimatorStep",
    "oqtopus_engine_core.steps.MultiManualStep",
    "oqtopus_engine_core.steps.ReadoutErrorMitigationStep",
    "oqtopus_engine_core.steps.TranquStep",
}


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
        config = self._inject_common_grpc_options(config)

        # Initialize the global context
        self._gctx = GlobalContext(config=config)
        logger.info("gctx.config=%s", mask_sensitive_info(self._gctx.config))

        if self._gctx.config.get("monitoring", {}).get("enabled", False):
            register_span_processor()
            instrument_clients()
            logger.info("monitoring enabled")

        # Initialize the DI container
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

    @staticmethod
    def _inject_common_grpc_options(config: dict) -> dict:
        """Inject top-level gRPC options into gRPC DI components.

        Returns:
            The config with common gRPC options added to relevant DI entries.

        """
        grpc_options = config.get("grpc")
        if not isinstance(grpc_options, dict) or not grpc_options:
            return config

        config_with_grpc = deepcopy(config)
        registry = config_with_grpc.get("di_container", {}).get("registry", {})
        for component in registry.values():
            if not isinstance(component, dict):
                continue
            if component.get("_target_") in GRPC_OPTION_TARGETS:
                component.setdefault("grpc_options", grpc_options)
        return config_with_grpc
