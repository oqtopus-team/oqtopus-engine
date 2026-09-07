import pytest
from pydantic import ValidationError

from oqtopus_engine_core.framework import PipelineBuilder, PipelineExecutor, PipelineManager
from oqtopus_engine_core.framework.buffer import Buffer
from oqtopus_engine_core.framework.pipeline_builder import (
    UnknownComponentError,
    UnknownStepError,
)
from oqtopus_util.di import DiContainer

# -----------------------
# Fake classes for testing
# -----------------------

class FakeStep:
    """A minimal fake Step used for testing PipelineBuilder."""
    pass


class FakeBuffer(Buffer):
    """A minimal fake Buffer used for testing PipelineBuilder.

    Inherits from the real Buffer ABC so `isinstance(_, Buffer)` checks in
    PipelineBuilder (shared-buffer classification) recognize it.
    """

    async def put(self, gctx, jctx, job):
        pass

    async def get(self):
        pass

    def size(self):
        return 0


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

def test_pipeline_builder_builds_manager_with_multiple_pipelines():
    """
    Test that PipelineBuilder.build constructs a PipelineManager with one
    PipelineExecutor per configured pipeline, each with the correct
    resolved, ordered list of steps, and that job_buffer resolves to the
    expected instance.
    """
    step1 = FakeStep()
    step2 = FakeStep()
    buffer = FakeBuffer()
    exception_handler = FakeExceptionHandler()

    registry = {
        "step1": step1,
        "step2": step2,
        "buffer": buffer,
        "exception_handler": exception_handler,
    }
    dicon = FakeDiContainer(registry)

    config = {
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
        "pipelines": [
            {"name": "a", "if": 'job.job_type == "a"', "steps": ["step1"]},
            {"name": "b", "if": 'job.job_type == "b"', "steps": ["step2"]},
        ],
    }

    manager = PipelineBuilder.build(config, dicon)

    assert isinstance(manager, PipelineManager)
    assert isinstance(manager._executors["a"], PipelineExecutor)
    assert isinstance(manager._executors["b"], PipelineExecutor)
    assert manager._executors["a"]._pipeline == [step1]
    assert manager._executors["b"]._pipeline == [step2]
    assert manager.job_buffer is buffer


def test_pipeline_builder_raises_on_duplicate_pipeline_name():
    """
    Test that duplicate pipeline names are rejected by the structural
    validation layer (Layer 1) before any DI resolution happens.
    """
    registry = {
        "step1": FakeStep(),
        "buffer": FakeBuffer(),
        "exception_handler": FakeExceptionHandler(),
    }
    dicon = FakeDiContainer(registry)

    config = {
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
        "pipelines": [
            {"name": "dup", "if": 'job.job_type == "a"', "steps": ["step1"]},
            {"name": "dup", "if": 'job.job_type == "b"', "steps": ["step1"]},
        ],
    }

    with pytest.raises(ValidationError):
        PipelineBuilder.build(config, dicon)


def test_pipeline_builder_raises_unknown_step_error():
    """
    Test that an unregistered step name raises UnknownStepError (Layer 2)
    instead of leaking a raw KeyError, confirming the builder still fails
    fast on misconfiguration.
    """
    registry = {
        "buffer": FakeBuffer(),
        "exception_handler": FakeExceptionHandler(),
    }
    dicon = FakeDiContainer(registry)

    config = {
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
        "pipelines": [
            {"name": "a", "if": "true", "steps": ["missing_step"]},
        ],
    }

    with pytest.raises(UnknownStepError):
        PipelineBuilder.build(config, dicon)


def test_pipeline_builder_raises_unknown_component_error_for_job_buffer():
    """
    Test that an unregistered job_buffer component raises
    UnknownComponentError (Layer 2).
    """
    registry = {
        "step1": FakeStep(),
        "exception_handler": FakeExceptionHandler(),
        # "buffer" is intentionally missing
    }
    dicon = FakeDiContainer(registry)

    config = {
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
        "pipelines": [
            {"name": "a", "if": "true", "steps": ["step1"]},
        ],
    }

    with pytest.raises(UnknownComponentError):
        PipelineBuilder.build(config, dicon)


def test_pipeline_builder_classifies_shared_buffer_by_identity():
    """
    Test that a Buffer instance referenced by more than one pipeline's
    `steps` is classified as "shared", along with the index it sits at
    within each referencing pipeline (see design doc 17.3章).
    """
    shared_buffer = FakeBuffer()
    registry = {
        "step1": FakeStep(),
        "buffer": shared_buffer,
        "exception_handler": FakeExceptionHandler(),
    }
    dicon = FakeDiContainer(registry)

    config = {
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
        "pipelines": [
            {"name": "a", "if": 'job.job_type == "a"', "steps": ["step1", "buffer"]},
            {"name": "b", "if": 'job.job_type == "b"', "steps": ["step1", "buffer"]},
        ],
    }

    manager = PipelineBuilder.build(config, dicon)

    assert shared_buffer in manager._shared_buffers
    assert manager._shared_buffers[shared_buffer] == {"a": 1, "b": 1}


def test_pipeline_builder_accepts_bare_yaml_true_as_condition():
    """
    YAML parses an unquoted `if: true` as a native Python bool, not the
    string "true" the condition DSL expects. PipelineBuilder must accept
    this without requiring config authors to quote it as `if: "true"`.
    """
    registry = {
        "step1": FakeStep(),
        "buffer": FakeBuffer(),
        "exception_handler": FakeExceptionHandler(),
    }
    dicon = FakeDiContainer(registry)

    config = {
        "job_buffer": "buffer",
        "exception_handler": "exception_handler",
        "pipelines": [
            {"name": "a", "if": True, "steps": ["step1"]},  # bare YAML bool
        ],
    }

    manager = PipelineBuilder.build(config, dicon)

    assert isinstance(manager, PipelineManager)


def test_pipeline_builder_treats_distinct_buffers_as_exclusive():
    """
    Test that two different Buffer instances (even under different DI
    names) are NOT classified as shared, so each pipeline's own
    PipelineExecutor keeps spawning their workers as before.
    """
    registry = {
        "step1": FakeStep(),
        "buffer_a": FakeBuffer(),
        "buffer_b": FakeBuffer(),
        "exception_handler": FakeExceptionHandler(),
    }
    dicon = FakeDiContainer(registry)

    config = {
        "job_buffer": "buffer_a",
        "exception_handler": "exception_handler",
        "pipelines": [
            {"name": "a", "if": 'job.job_type == "a"', "steps": ["step1", "buffer_a"]},
            {"name": "b", "if": 'job.job_type == "b"', "steps": ["step1", "buffer_b"]},
        ],
    }

    manager = PipelineBuilder.build(config, dicon)

    assert manager._shared_buffers == {}
