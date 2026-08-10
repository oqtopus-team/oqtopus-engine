import asyncio

import pytest

from oqtopus_engine_core.framework.buffer import Buffer
from oqtopus_engine_core.framework.context import GlobalContext, JobContext
from oqtopus_engine_core.framework.exception_handler import PipelineExceptionHandler
from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.framework.pipeline import PipelineExecutor
from oqtopus_engine_core.framework.pipeline_condition import compile_condition
from oqtopus_engine_core.framework.pipeline_manager import (
    NoMatchingPipelineError,
    PipelineManager,
    PipelineSelector,
)
from oqtopus_engine_core.framework.step import Step, StepResult

# ---------------------------------------------------------------------------
# Helper factory functions
# ---------------------------------------------------------------------------

def make_test_job(job_id: str, job_type: str = "root") -> Job:
    """Create a minimal but valid Job instance for pipeline manager tests."""
    return Job(
        job_id=job_id,
        job_type=job_type,
        device_id="test-device",
        shots=100,
        input="test-input",
        program=[],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="CREATED",
    )


def make_test_global_context() -> GlobalContext:
    """Create a minimal GlobalContext instance for pipeline manager tests."""
    return GlobalContext(config={})


def make_selector(*name_and_condition: tuple[str, str]) -> PipelineSelector:
    """Build a PipelineSelector from `(name, condition_string)` pairs."""
    return PipelineSelector(
        [(name, compile_condition(condition)) for name, condition in name_and_condition]
    )


# ---------------------------------------------------------------------------
# Helper fakes
# ---------------------------------------------------------------------------

class RecordStep(Step):
    """Record which job_ids reach this step during pre-process."""

    def __init__(self):
        self.job_ids = []

    async def pre_process(self, gctx, jctx, job):
        self.job_ids.append(job.job_id)
        return StepResult()

    async def post_process(self, gctx, jctx, job):
        return StepResult()


class ConcurrencyTrackingStep(Step):
    """Record job_ids seen and the max number of concurrently in-flight jobs."""

    def __init__(self):
        self.job_ids = []
        self.current = 0
        self.max_seen = 0

    async def pre_process(self, gctx, jctx, job):
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        await asyncio.sleep(0.05)
        self.current -= 1
        self.job_ids.append(job.job_id)
        return StepResult()

    async def post_process(self, gctx, jctx, job):
        return StepResult()


class FakeBuffer(Buffer):
    """Buffer-like object backed by a real asyncio.Queue."""

    def __init__(self, max_concurrency=1):
        self._queue = asyncio.Queue()
        self._max_concurrency = max_concurrency

    async def put(self, gctx, jctx, job):
        await self._queue.put((gctx, jctx, job))

    async def get(self):
        return await self._queue.get()

    def size(self):
        return self._queue.qsize()

    @property
    def max_concurrency(self):
        return self._max_concurrency


class FakeExceptionHandler(PipelineExceptionHandler):
    """Record every exception passed to handle_exception."""

    def __init__(self):
        self.handled = []

    async def handle_exception(self, ex, gctx, jctx, job):
        self.handled.append((ex, gctx, jctx, job))


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_pipeline_routes_job_to_matching_pipeline():
    record_a, record_b = RecordStep(), RecordStep()
    exception_handler = FakeExceptionHandler()

    manager = PipelineManager(
        executors={
            "a": PipelineExecutor([record_a], FakeBuffer(), exception_handler),
            "b": PipelineExecutor([record_b], FakeBuffer(), exception_handler),
        },
        selector=make_selector(
            ("a", 'job.job_type == "a"'),
            ("b", 'job.job_type == "b"'),
        ),
        shared_buffers={},
        exception_handler=exception_handler,
        job_buffer=FakeBuffer(),
    )

    job_a = make_test_job("job-a", job_type="a")
    await manager.execute_pipeline(
        make_test_global_context(), JobContext(initial={}), job_a
    )
    await asyncio.sleep(0)

    assert record_a.job_ids == ["job-a"]
    assert record_b.job_ids == []


@pytest.mark.asyncio
async def test_execute_pipeline_fails_job_when_no_pipeline_matches():
    """12.6.1章: no matching pipeline -> exception_handler receives NoMatchingPipelineError."""
    exception_handler = FakeExceptionHandler()

    manager = PipelineManager(
        executors={
            "a": PipelineExecutor([RecordStep()], FakeBuffer(), exception_handler)
        },
        selector=make_selector(("a", 'job.job_type == "a"')),
        shared_buffers={},
        exception_handler=exception_handler,
        job_buffer=FakeBuffer(),
    )

    job = make_test_job("job-x", job_type="unmatched_type")
    await manager.execute_pipeline(
        make_test_global_context(), JobContext(initial={}), job
    )

    assert len(exception_handler.handled) == 1
    ex, _gctx, _jctx, handled_job = exception_handler.handled[0]
    assert isinstance(ex, NoMatchingPipelineError)
    assert handled_job.job_id == "job-x"


@pytest.mark.asyncio
async def test_job_buffer_property_returns_configured_instance():
    job_buffer = FakeBuffer()
    manager = PipelineManager(
        executors={},
        selector=make_selector(),
        shared_buffers={},
        exception_handler=FakeExceptionHandler(),
        job_buffer=job_buffer,
    )

    assert manager.job_buffer is job_buffer


@pytest.mark.asyncio
async def test_shared_buffer_resumes_correct_pipeline():
    """
    Regression test for the worker/index cross-pipeline bug found while
    designing the shared-buffer routing (design doc 17.3章).

    Two pipelines share the same Buffer instance at different positions.
    A job routed to pipeline "a" must resume into pipeline "a"'s own
    downstream step, never pipeline "b"'s, and vice versa.
    """
    shared_buffer = FakeBuffer()
    exception_handler = FakeExceptionHandler()
    record_a, record_b = RecordStep(), RecordStep()

    pipeline_a = [RecordStep(), shared_buffer, record_a]  # buffer at index 1
    pipeline_b = [RecordStep(), RecordStep(), shared_buffer, record_b]  # buffer at index 2

    executor_a = PipelineExecutor(
        pipeline_a,
        shared_buffer,
        exception_handler,
        externally_managed_buffers=frozenset({shared_buffer}),
    )
    executor_b = PipelineExecutor(
        pipeline_b,
        shared_buffer,
        exception_handler,
        externally_managed_buffers=frozenset({shared_buffer}),
    )

    manager = PipelineManager(
        executors={"a": executor_a, "b": executor_b},
        selector=make_selector(
            ("a", 'job.job_type == "a"'),
            ("b", 'job.job_type == "b"'),
        ),
        shared_buffers={shared_buffer: {"a": 1, "b": 2}},
        exception_handler=exception_handler,
        job_buffer=shared_buffer,
    )

    # Not awaited to completion: start() spawns workers and then blocks
    # forever, mirroring how the existing PipelineExecutor tests exercise
    # start() (see test_executor_workers_call_buffer_get).
    asyncio.create_task(manager.start())

    job_a = make_test_job("job-a", job_type="a")
    job_b = make_test_job("job-b", job_type="b")
    await manager.execute_pipeline(
        make_test_global_context(), JobContext(initial={}), job_a
    )
    await manager.execute_pipeline(
        make_test_global_context(), JobContext(initial={}), job_b
    )

    await asyncio.sleep(0.05)

    assert record_a.job_ids == ["job-a"]
    assert record_b.job_ids == ["job-b"]


@pytest.mark.asyncio
async def test_shared_buffer_worker_falls_back_to_selector_when_pipeline_name_missing():
    """
    Regression test for MpAutoCombiningBuffer.create_combined_job(), which
    builds a brand-new JobContext for a combined job instead of reusing an
    original job's jctx, so the combined job's jctx has no "pipeline_name".

    The shared worker must re-derive the pipeline via the selector (based
    on the dequeued job's own job_type) instead of dropping the job.
    """
    shared_buffer = FakeBuffer()
    exception_handler = FakeExceptionHandler()
    record_a = RecordStep()

    pipeline_a = [RecordStep(), shared_buffer, record_a]  # buffer at index 1

    executor_a = PipelineExecutor(
        pipeline_a,
        shared_buffer,
        exception_handler,
        externally_managed_buffers=frozenset({shared_buffer}),
    )

    manager = PipelineManager(
        executors={"a": executor_a},
        selector=make_selector(("a", 'job.job_type == "a"')),
        shared_buffers={shared_buffer: {"a": 1}},
        exception_handler=exception_handler,
        job_buffer=shared_buffer,
    )

    asyncio.create_task(manager.start())

    # Simulate what MpAutoCombiningBuffer.create_combined_job() does: put an
    # item directly into the buffer with a fresh JobContext, bypassing
    # execute_pipeline() (so "pipeline_name" is never set on it).
    combined_job = make_test_job("combined-job", job_type="a")
    await shared_buffer.put(make_test_global_context(), JobContext(), combined_job)

    await asyncio.sleep(0.05)

    assert record_a.job_ids == ["combined-job"]
    assert exception_handler.handled == []


@pytest.mark.asyncio
async def test_shared_buffer_max_concurrency_is_global_across_pipelines():
    """
    buffer.max_concurrency workers are spawned once per shared Buffer
    *object*, not once per pipeline that references it. With
    max_concurrency=2 and two pipelines sharing the buffer, at most 2 jobs
    may be in flight past the buffer at once, combined across both
    pipelines (not 2 workers x 2 pipelines = 4).
    """
    shared_buffer = FakeBuffer(max_concurrency=2)
    exception_handler = FakeExceptionHandler()
    tracker = ConcurrencyTrackingStep()

    pipeline_a = [shared_buffer, tracker]
    pipeline_b = [shared_buffer, tracker]

    executor_a = PipelineExecutor(
        pipeline_a,
        shared_buffer,
        exception_handler,
        externally_managed_buffers=frozenset({shared_buffer}),
    )
    executor_b = PipelineExecutor(
        pipeline_b,
        shared_buffer,
        exception_handler,
        externally_managed_buffers=frozenset({shared_buffer}),
    )

    manager = PipelineManager(
        executors={"a": executor_a, "b": executor_b},
        selector=make_selector(
            ("a", 'job.job_type == "a"'),
            ("b", 'job.job_type == "b"'),
        ),
        shared_buffers={shared_buffer: {"a": 0, "b": 0}},
        exception_handler=exception_handler,
        job_buffer=shared_buffer,
    )

    asyncio.create_task(manager.start())

    jobs = [
        make_test_job(f"job-{i}", job_type="a" if i % 2 == 0 else "b")
        for i in range(6)
    ]
    for job in jobs:
        await manager.execute_pipeline(
            make_test_global_context(), JobContext(initial={}), job
        )

    await asyncio.sleep(0.3)

    assert sorted(tracker.job_ids) == sorted(j.job_id for j in jobs)
    assert tracker.max_seen == 2
