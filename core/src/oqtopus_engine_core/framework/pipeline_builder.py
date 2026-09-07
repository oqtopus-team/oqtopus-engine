from typing import TYPE_CHECKING, Any

from oqtopus_util.di import DiContainer

from .buffer import Buffer
from .pipeline import PipelineExecutor
from .pipeline_condition import Expr, compile_condition
from .pipeline_config import PipelineManagerConfig
from .pipeline_manager import PipelineManager, PipelineSelector
from .step import Step

if TYPE_CHECKING:
    from .exception_handler import PipelineExceptionHandler


class PipelineBuildError(Exception):
    """Base class for errors raised while building a PipelineManager."""


class UnknownStepError(PipelineBuildError):
    """Raised when a `steps` entry is not registered in the DI container."""


class UnknownComponentError(PipelineBuildError):
    """Raised when `job_buffer`/`exception_handler` is not registered in the DI."""


class PipelineBuilder:
    """PipelineBuilder: Construct a PipelineManager from config + DI Container.

    This builder focuses *only* on building the PipelineManager.
    It does NOT wire job fetchers, device fetchers, or repositories.
    Those belong to the application initialization layer (app.py).
    """

    @staticmethod
    def build(
        pipeline_manager_config: dict[str, Any],
        dicon: DiContainer,
    ) -> PipelineManager:
        """Build a PipelineManager instance from the given configuration.

        Expected config format (under the `pipeline_manager` root key):

            pipeline_manager:
              job_buffer: buffer
              exception_handler: exception_handler
              pipelines:
                - name: sampling
                  if: job.job_type == "sampling"
                  steps:
                    - job_repository_update_step
                    - tranqu_step
                    - buffer
                    - device_gateway_step
                - name: sse
                  if: job.job_type == "sse"
                  steps:
                    - job_repository_update_step
                    - sse_step

        Args:
            pipeline_manager_config: The configuration dictionary, validated
                against `PipelineManagerConfig` (structural checks: pipeline
                name uniqueness, non-empty `pipelines`/`steps`, ...).
            dicon: The dependency injection container used to resolve
                component instances.

        Returns:
            PipelineManager: A fully constructed PipelineManager instance.

        """
        # Layer 1: structural validation (raises pydantic.ValidationError).
        config = PipelineManagerConfig.model_validate(pipeline_manager_config)

        # Layer 2a: compile & type-check each pipeline's `if` condition.
        compiled_conditions: list[tuple[str, Expr]] = [
            (pdef.name, compile_condition(pdef.if_)) for pdef in config.pipelines
        ]
        selector = PipelineSelector(compiled_conditions)

        # Layer 2b: resolve each pipeline's `steps` via the DI container.
        steps_by_pipeline: dict[str, list[Step | Buffer]] = {
            pdef.name: [
                PipelineBuilder._resolve_step(name, dicon) for name in pdef.steps
            ]
            for pdef in config.pipelines
        }

        # Buffers referenced by more than one pipeline get their workers
        # spawned centrally by PipelineManager instead of by each individual
        # PipelineExecutor (see design doc 17.3章 for why this is required
        # for correctness, not just style).
        shared_buffers = PipelineBuilder._classify_shared_buffers(steps_by_pipeline)

        job_buffer = PipelineBuilder._resolve_component(config.job_buffer, dicon)
        exception_handler: PipelineExceptionHandler = (
            PipelineBuilder._resolve_component(config.exception_handler, dicon)
        )

        executors = {
            name: PipelineExecutor(
                pipeline=steps,
                job_buffer=job_buffer,
                exception_handler=exception_handler,
                externally_managed_buffers=frozenset(shared_buffers),
            )
            for name, steps in steps_by_pipeline.items()
        }

        return PipelineManager(
            executors=executors,
            selector=selector,
            shared_buffers=shared_buffers,
            exception_handler=exception_handler,
            job_buffer=job_buffer,
        )

    @staticmethod
    def _resolve_step(name: str, dicon: DiContainer) -> Step | Buffer:
        try:
            return dicon.get(name)
        except KeyError as exc:
            message = f"unknown step or buffer component: {name!r}"
            raise UnknownStepError(message) from exc

    @staticmethod
    def _resolve_component(name: str, dicon: DiContainer) -> Any:  # noqa: ANN401
        try:
            return dicon.get(name)
        except KeyError as exc:
            message = f"unknown component: {name!r}"
            raise UnknownComponentError(message) from exc

    @staticmethod
    def _classify_shared_buffers(
        steps_by_pipeline: dict[str, list[Step | Buffer]],
    ) -> dict[Buffer, dict[str, int]]:
        """Find Buffer instances referenced by more than one pipeline.

        Classification is by object identity, not by name: two pipelines
        that happen to resolve the same DI singleton to the same Buffer
        object are "shared"; distinct Buffer objects (e.g. different DI
        names, or a `prototype`-scoped component) are "exclusive" and keep
        the existing per-executor worker-spawning behavior.

        Returns:
            Mapping from shared Buffer instance to
            `{pipeline_name: index_in_that_pipeline}`.

        """
        index_by_pipeline: dict[Buffer, dict[str, int]] = {}
        for name, steps in steps_by_pipeline.items():
            for index, node in enumerate(steps):
                if isinstance(node, Buffer):
                    index_by_pipeline.setdefault(node, {})[name] = index

        return {
            buffer: indices
            for buffer, indices in index_by_pipeline.items()
            if len(indices) > 1
        }
