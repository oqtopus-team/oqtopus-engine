import pytest

from oqtopus_engine_core.framework import PipelineBuilder, PipelineExecutor
from oqtopus_engine_core.utils.di_container import DiContainer

# -----------------------
# Fake classes for testing
# -----------------------

class FakeStep:
    """A minimal fake Step used for testing PipelineBuilder."""
    pass


class FakeBuffer:
    """A minimal fake Buffer used for testing PipelineBuilder."""
    pass


class FakeExceptionHandler:
    """A minimal fake exception handler for testing."""
    pass


# --------------------------------
# Simple Fake DI Container for testing
# --------------------------------

class FakeDiContainer(DiContainer):
    """
    A simple mock of DiContainer that stores objects in a registry
    and returns them when get(name) is called.
    """

    def __init__(self, registry):
        self._registry = registry

    def get(self, name):
        return self._registry[name]


# --------------------------------
# Test Cases
# --------------------------------

def test_pipeline_builder_builds_executor_correctly():
    """
    Test that PipelineBuilder.build constructs a PipelineExecutor with:

      - The correct ordered list of pipeline components
      - The correct job buffer instance
      - The correct exception handler instance

    This ensures that PipelineBuilder performs DI lookups correctly
    and passes the resolved objects to PipelineExecutor.
    """

    # Arrange: create fake components
    step1 = FakeStep()
    step2 = FakeStep()
    buffer = FakeBuffer()
    exception_handler = FakeExceptionHandler()

    # Fake DI registry
    registry = {
        "step1": step1,
        "step2": step2,
        "buffer": buffer,
        "exception_handler": exception_handler,
    }

    dicon = FakeDiContainer(registry)

    # Config following the expected format
    pipeline_config = {
        "pipeline": ["step1", "step2", "buffer"],
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
    }

    # Act
    executor = PipelineBuilder.build(pipeline_config, dicon)

    # Assert
    assert isinstance(executor, PipelineExecutor)
    assert executor._pipeline == [step1, step2, buffer]
    assert executor._job_buffer is buffer
    assert executor._exception_handler is exception_handler


def test_pipeline_builder_raises_key_error_if_missing_component():
    """
    Test that PipelineBuilder.build raises a KeyError if any component
    listed in the config is missing from the DI container.

    This confirms that the builder does not silently ignore unresolved
    components and fails fast when configuration is incorrect.
    """

    # Fake DI without all necessary components
    registry = {
        "step1": FakeStep(),
        # step2 is missing
        "buffer": FakeBuffer(),
        "exception_handler": FakeExceptionHandler(),
    }
    dicon = FakeDiContainer(registry)

    pipeline_config = {
        "pipeline": ["step1", "step2"],   # step2 will cause KeyError
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
    }

    with pytest.raises(KeyError):
        PipelineBuilder.build(pipeline_config, dicon)
