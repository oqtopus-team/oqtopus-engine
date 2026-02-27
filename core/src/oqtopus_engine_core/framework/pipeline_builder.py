from typing import TYPE_CHECKING, Any

from oqtopus_engine_core.framework import (
    Buffer,
    PipelineExceptionHandler,
    PipelineExecutor,
)
from oqtopus_engine_core.utils.di_container import DiContainer

if TYPE_CHECKING:
    from oqtopus_engine_core.framework.step import Step


class PipelineBuilder:
    """PipelineBuilder: Construct a PipelineExecutor from config + DI Container.

    This builder focuses *only* on building the PipelineExecutor.
    It does NOT wire job fetchers, device fetchers, or repositories.
    Those belong to the application initialization layer (app.py).
    """

    @staticmethod
    def build(
        pipeline_config: dict[str, Any],
        dicon: DiContainer,
    ) -> PipelineExecutor:
        """Build a PipelineExecutor instance from the given pipeline configuration.

        Expected config format (under the `pipeline_executor` root key):

            pipeline_executor:
              pipeline:
                - job_repository_update_step
                - multi_manual_step
                - tranqu_step
                - estimator_step
                - ro_error_mitigation_step
                - buffer
                - sse_step
                - device_gateway_step
              job_buffer: buffer
              exception_handler: exception_handler

        Args:
            pipeline_config : Dict[str, Any]
                The configuration dictionary containing:
                - "pipeline": list of component names in order
                - "job_buffer": name of the buffer component
                - "exception_handler": name of the exception handler component
            dicon : DiContainer
                The dependency injection container used to resolve component instances.

        Returns:
            PipelineExecutor: A fully constructed PipelineExecutor instance.

        """
        # pipeline nodes (steps and buffers)
        pipeline: list[Buffer | Step] = [
            dicon.get(name) for name in pipeline_config["pipeline"]
        ]

        # job buffer
        job_buffer: Buffer = dicon.get(pipeline_config["job_buffer"])

        # exception handler
        exception_handler: PipelineExceptionHandler = dicon.get(
            pipeline_config["exception_handler"]
        )

        # Construct PipelineExecutor
        return PipelineExecutor(
            pipeline=pipeline,
            job_buffer=job_buffer,
            exception_handler=exception_handler,
        )
