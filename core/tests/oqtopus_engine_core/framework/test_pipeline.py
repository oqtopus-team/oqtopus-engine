import asyncio

import pytest

from oqtopus_engine_core.buffers import QueueBuffer
from oqtopus_engine_core.framework.buffer import Buffer
from oqtopus_engine_core.framework.context import GlobalContext, JobContext
from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.framework.pipeline import PipelineExecutor, StepPhase
from oqtopus_engine_core.framework.step import PipelineDirective, Step, StepResult

# ---------------------------------------------------------------------------
# Helper factory functions
# ---------------------------------------------------------------------------

def make_test_job(job_id: str, job_type: str = "root") -> Job:
    """Create a minimal but valid Job instance for pipeline tests."""
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
    """Create a minimal GlobalContext instance for pipeline tests."""
    return GlobalContext(config={})

# ---------------------------------------------------------------------------
# Helper Step Implementations
# ---------------------------------------------------------------------------

class SplitOnPreStep(Step):
    """Create two children during pre-process (SPLIT_FOR_JOIN)."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        child_jobs = []
        child_ctxs = []
        for i in range(2):
            c_job = make_test_job(job_id=f"{job.job_id}-child{i}", job_type="child")
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)
        return StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_jobs=child_jobs,
            child_contexts=child_ctxs,
        )

    async def post_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()


class SplitOnPostStep(Step):
    """Create two children during post-process (SPLIT_WITHOUT_JOIN).

    Only splits for root jobs (job.parent is None); children pass through.
    """

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        if job.parent is not None:
            return StepResult()
        child_jobs = []
        child_ctxs = []
        for i in range(2):
            c_job = make_test_job(job_id=f"{job.job_id}-child{i}", job_type="child")
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)
        return StepResult(
            directive=PipelineDirective.SPLIT_WITHOUT_JOIN,
            child_jobs=child_jobs,
            child_contexts=child_ctxs,
        )


class JoinOnPreStep(Step):
    """Signal JOIN in pre-process phase (for split children only)."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        if job.parent is not None:
            return StepResult(directive=PipelineDirective.JOIN)
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child) -> None:
        parent_jctx["pre_join"] = True


class JoinOnPostStep(Step):
    """Signal JOIN in post-process phase (for split children only)."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        if job.parent is not None:
            return StepResult(directive=PipelineDirective.JOIN)
        return StepResult()

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child) -> None:
        parent_jctx["joined"] = True


class RecordStep(Step):
    """Record execution order in pre- and post-phases."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        jctx.setdefault("record", []).append(("pre", self.__class__.__name__))
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        jctx["record"].append(("post", self.__class__.__name__))
        return StepResult()


class ErrorStep(Step):
    """Raise RuntimeError during pre-process to simulate child failure."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        raise RuntimeError("child exploded")

    async def post_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()


class SlowJoinStep(Step):
    """Join step with artificial delay to force race conditions."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        await asyncio.sleep(0.01)
        if job.parent is not None:
            return StepResult(directive=PipelineDirective.JOIN)
        return StepResult()

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child) -> None:
        parent_jctx["joined"] = last_child.job_id


class FlagStep(Step):
    """Set a flag during post-process to confirm execution."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        jctx["flag"] = True
        return StepResult()


class CountJoinStep(Step):
    """Count how many times join_jobs is called."""

    def __init__(self) -> None:
        self.calls = 0

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        if job.parent is not None:
            return StepResult(directive=PipelineDirective.JOIN)
        return StepResult()

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child) -> None:
        self.calls += 1
        parent_jctx["joined"] = True


class SplitJoinSameStep(Step):
    """Split in pre-process and join on this same step in post-process."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        if job.parent is not None:
            return StepResult()
        child_jobs = []
        child_ctxs = []
        for i in range(2):
            c_job = make_test_job(job_id=f"{job.job_id}-child{i}", job_type=job.job_type)
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)
        return StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_jobs=child_jobs,
            child_contexts=child_ctxs,
        )

    async def post_process(self, gctx, jctx, job) -> StepResult:
        if job.parent is not None:
            return StepResult(directive=PipelineDirective.JOIN)
        return StepResult()

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child) -> None:
        parent_jctx["same_step_joined"] = last_child.job_id


class TripleSplitStep(Step):
    """Generate 3 children during pre-process (SPLIT_FOR_JOIN)."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        child_jobs = []
        child_ctxs = []
        for i in range(3):
            c_job = make_test_job(f"{job.job_id}-child{i}", job_type="child")
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)
        return StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_jobs=child_jobs,
            child_contexts=child_ctxs,
        )

    async def post_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()


class ErrorInPostStep(Step):
    """Raise RuntimeError during post-process."""

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        raise RuntimeError("post exploded")


class SlowPostStep(Step):
    """Step whose post_process blocks for 1 second.

    Prevents worker threads from re-entering the buffer.get() loop so each
    worker calls get() exactly once.
    """

    async def pre_process(self, gctx, jctx, job) -> StepResult:
        return StepResult()

    async def post_process(self, gctx, jctx, job) -> StepResult:
        await asyncio.sleep(1.0)
        return StepResult()

# ---------------------------------------------------------------------------
# Helper Buffer Implementations
# ---------------------------------------------------------------------------

class DummyBuffer(QueueBuffer):
    """A no-op buffer used to simulate an unexpected buffer in POST_PROCESS."""


class FakeBuffer(Buffer):
    """Buffer-like object with configurable max_concurrency and deterministic get()."""

    def __init__(self, max_concurrency=1):
        self._queue = asyncio.Queue()
        self._max_concurrency = max_concurrency
        self.get_calls = 0

    async def put(self, gctx, jctx, job):
        await self._queue.put((gctx, jctx, job))

    async def get(self):
        """Simulate a blocking worker call with short delay."""
        self.get_calls += 1
        await asyncio.sleep(0)  # let event loop switch tasks
        return await self._queue.get()

    def size(self):
        return self._queue.qsize()

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_split_join_parent_resume():
    """
    Normal flow: split → children → join → parent post-process resume.
    """
    pipeline = [
        RecordStep(),
        SplitOnPreStep(),
        RecordStep(),
        JoinOnPostStep(),
        RecordStep(),
    ]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    root_job = make_test_job("root")
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, root_job
    )

    assert jctx["joined"] is True
    assert ("post", "RecordStep") in jctx["record"]
    assert jctx.step_history == [
        ("pre_process", 0),
        ("pre_process", 1),
        ("post_process", 2),
        ("post_process", 1),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 2),
            ("pre_process", 3),
            ("pre_process", 4),
            ("post_process", 4),
            ("post_process", 3),
        ]


@pytest.mark.asyncio
async def test_child_error_cleans_pending_children():
    """
    A child fails before join → pending_children must be removed.
    """
    pipeline = [SplitOnPreStep(), ErrorStep(), JoinOnPostStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert not executor._pending_children
    assert jctx.step_history == [
        ("pre_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
        ]


@pytest.mark.asyncio
async def test_race_safe_last_child():
    """
    Race-safe join: only exactly one child becomes last_child.
    """
    pipeline = [SplitOnPreStep(), SlowJoinStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx["joined"] in ("root-child0", "root-child1")
    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("post_process", 1),
        ]


@pytest.mark.asyncio
async def test_split_on_preprocess_and_postprocess():
    """
    Nested split should not crash.
    """
    pipeline = [SplitOnPreStep(), SplitOnPostStep(), JoinOnPostStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 1),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("post_process", 0),
        ]


@pytest.mark.asyncio
async def test_parent_post_runs_after_join():
    """
    After join is complete, parent pre-process should continue to following steps.
    """
    pipeline = [SplitOnPreStep(), JoinOnPostStep(), FlagStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx["joined"] is True
    for child_jctx in jctx.children:
        assert child_jctx["flag"] is True

    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("pre_process", 2),
            ("post_process", 2),
            ("post_process", 1),
        ]


@pytest.mark.asyncio
async def test_parent_reaching_join_does_not_trigger_join():
    """
    A root job (no parent) reaching a join step returns NONE from post_process.
    join_jobs is never called and the pipeline completes normally.
    """
    join_step = CountJoinStep()
    pipeline = [join_step, FlagStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert join_step.calls == 0
    assert jctx["flag"] is True
    assert jctx.step_history == [
        ("pre_process", 0),
        ("pre_process", 1),
        ("post_process", 1),
        ("post_process", 0),
    ]


@pytest.mark.asyncio
async def test_split_three_children_join_correct_last():
    """
    Split producing 3 children → join must run once with correct last_child.
    """
    pipeline = [TripleSplitStep(), SlowJoinStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx["joined"] in (
        "root-child0",
        "root-child1",
        "root-child2",
    )

    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("post_process", 1),
        ]


@pytest.mark.asyncio
async def test_join_jobs_called_once():
    """
    join_jobs must be called exactly once even with race conditions.
    """
    join_step = CountJoinStep()
    pipeline = [SplitOnPreStep(), join_step]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert join_step.calls == 1
    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("post_process", 1),
        ]


@pytest.mark.asyncio
async def test_same_step_split_pre_and_join_post():
    """
    A step that splits in pre-process and joins on the same step's post-process.
    """
    pipeline = [SplitJoinSameStep(), RecordStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx["same_step_joined"] in ("root-child0", "root-child1")
    assert jctx.step_history == [
        ("pre_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("post_process", 1),
            ("post_process", 0),
        ]


@pytest.mark.asyncio
async def test_child_post_error_cleans_pending_children():
    """
    A child throwing error during POST must also clean pending_children.
    """
    pipeline = [SplitOnPreStep(), ErrorInPostStep(), JoinOnPostStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert not executor._pending_children
    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 1),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("pre_process", 2),
            ("post_process", 2),
        ]


@pytest.mark.asyncio
async def test_join_at_pipeline_end_no_resume():
    """
    JoinStep at end of pipeline → join occurs but no parent resume should happen.
    """
    pipeline = [SplitOnPreStep(), JoinOnPostStep()]  # No steps after join
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx["joined"] is True
    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("post_process", 1),
        ]


@pytest.mark.asyncio
async def test_join_on_preprocess_runs_only_for_children():
    """
    JoinOnPreStep should fire only for children (job.parent is not None), not for parent.
    """
    pipeline = [SplitOnPreStep(), JoinOnPreStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx.get("pre_join") is True
    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 1),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
        ]


@pytest.mark.asyncio
async def test_join_and_flag_both_present():
    """
    Ensure joined flag and another POST flag can co-exist properly.
    """
    pipeline = [
        SplitOnPreStep(),
        SlowJoinStep(),
        FlagStep(),
    ]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert "joined" in jctx
    for child_jctx in jctx.children:
        assert child_jctx["flag"] is True

    assert jctx.step_history == [
        ("pre_process", 0),
        ("post_process", 0),
    ]
    for child_jctx in jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("pre_process", 2),
            ("post_process", 2),
            ("post_process", 1),
        ]


@pytest.mark.asyncio
async def test_buffer_in_pipeline():
    """
    Test the standard execution flow of a pipeline containing a Buffer node.

    This test verifies the following:
      - The PipelineExecutor correctly traverses through a pipeline that
        includes a Buffer node.
      - During PRE_PROCESS, the execution moves forward from index 0 to 2.
      - During POST_PROCESS, the execution moves backward from index 2 to 0.
      - The step history correctly records the full bidirectional traversal
        including the Buffer node's position.

    Expected behavior:
      - The pipeline completes successfully.
      - Each step (including Buffer) is visited in the correct order for both
        PRE_PROCESS and POST_PROCESS phases.
    """

    buffer = DummyBuffer()
    pipeline = [
        RecordStep(),
        buffer,
        RecordStep(),
    ]

    executor = PipelineExecutor(pipeline, buffer)
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    # When _run_from reaches a Buffer during PRE_PROCESS, it performs buffer.put(...)
    # and then returns, delegating the continuation to a worker. In unit tests, no
    # workers are running, so we manually resume execution from the next step.
    next_gctx, next_jctx, next_job = await buffer.get()
    await executor._run_from(
        StepPhase.PRE_PROCESS,
        2,
        next_gctx,
        next_jctx,
        next_job,
    )
    assert jctx.step_history == [
        ("pre_process", 0),
        ("pre_process", 1),
        ("pre_process", 2),
        ("post_process", 2),
        ("post_process", 1),
        ("post_process", 0),
    ]

@pytest.mark.asyncio
async def test_split_only_triggers_cascade_cleanup():
    """
    Split-only pipeline (no join) must decrement pending_children and
    remove the parent's entry via cascade_cleanup.

    Scenario:
      - A parent job performs split and stops execution.
      - Each child fully runs PRE_PROCESS → POST_PROCESS and finishes the pipeline.
      - Because no join occurs, cascade_cleanup must decrement the
        parent's pending_children and delete it when reaching zero.

    Expected:
      - executor._pending_children becomes empty after children complete.
      - No resource leak remains.
    """
    pipeline = [SplitOnPreStep(), RecordStep()]  # No join step
    executor = PipelineExecutor(pipeline, QueueBuffer())

    jctx = JobContext(initial={})
    root_job = make_test_job("root")

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, root_job
    )

    # After children complete, all pending_children should be cleaned.
    assert executor._pending_children == {}


@pytest.mark.asyncio
async def test_nested_split_only_cascade_cleanup():
    """
    Multi-level split-only pipeline must perform recursive cascade cleanup.

    Scenario:
      - root splits into children.
      - Each child performs another split into grandchildren.
      - No join step exists; all jobs finish normally.
      - cascade_cleanup must propagate upward through 2 levels.

    Expected:
      - executor._pending_children becomes empty (root-level entry removed).
      - No resource leak remains.
    """
    pipeline = [SplitOnPreStep(), SplitOnPreStep()]  # Nested split, no join
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    root_job = make_test_job("root")
    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, root_job
    )

    assert executor._pending_children == {}


@pytest.mark.asyncio
async def test_broken_buffer_does_not_cleanup_children():
    """
    If Buffer.get fails before returning a job, the executor cannot know
    which parent to clean up. In this case, pending_children is not
    decremented and the error is only logged by the worker.

    This test documents that behavior: cleanup is not guaranteed when
    the infrastructure (Buffer) itself is broken.
    """
    class BrokenBuffer(QueueBuffer):
        async def get(self):
            raise RuntimeError("buffer broke")

    buffer = BrokenBuffer()
    pipeline = [SplitOnPreStep(), buffer]
    executor = PipelineExecutor(pipeline, buffer)
    jctx = JobContext(initial={})
    root_job = make_test_job("root")

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, root_job
    )

    # Children were created, but no worker consumed them.
    # The executor cannot safely clean up pending_children here.
    assert executor._pending_children == {"root": 2}


@pytest.mark.asyncio
async def test_split_buffer_split_cascade_cleanup():
    """
    split → split → no join

    This test ensures that cascade cleanup works correctly even when Buffers
    interrupt execution between split steps.

    Expected:
      - Both split levels allocate pending_children.
      - All children resume and finish.
      - Final cleanup must clear every pending_children entry.
    """

    pipeline = [
        SplitOnPreStep(),
        SplitOnPreStep(),  # split again
        RecordStep(),
    ]

    executor = PipelineExecutor(pipeline, pipeline[1])
    jctx = JobContext(initial={})
    root_job = make_test_job("root")

    # First _run_from: hitting the buffer causes executor to return early.
    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, root_job
    )

    # All pending_children must be released.
    assert executor._pending_children == {}


@pytest.mark.asyncio
async def test_executor_respects_buffer_max_concurrency():
    """
    start() must spawn exactly buffer.max_concurrency() workers.
    """
    buffer = FakeBuffer(max_concurrency=3)

    # Pipeline includes a buffer so that the executor registers worker loops.
    pipeline = [RecordStep(), buffer, RecordStep()]
    executor = PipelineExecutor(pipeline, buffer)

    # Prepare one job in the buffer so workers have something to consume.
    gctx = GlobalContext(config={})
    jctx = JobContext(initial={})
    job = make_test_job(job_id="test-job")
    await buffer.put(gctx, jctx, job)

    # Immediately schedule stop so start() exits after workers are created.
    executor._stop_event.set()

    await executor.start()

    # Workers created must match max_concurrency.
    assert len(executor._workers) == 3


@pytest.mark.asyncio
async def test_executor_workers_call_buffer_get():
    """
    Test that each worker calls buffer.get() exactly once.

    By using SlowPostStep, each worker becomes blocked in post_process for 1 second.
    This prevents the PipelineExecutor's worker loop from retrying buffer.get(),
    ensuring deterministic behavior:
      - max_concurrency = 2 → 2 workers
      - each calls get() once → get_calls == 2

    A sleep(1.0) in the test ensures the event loop has enough time to
    schedule all workers and let them reach SlowPostStep.post_process.
    """

    buffer = FakeBuffer(max_concurrency=2)

    pipeline = [SlowPostStep(), buffer]
    executor = PipelineExecutor(pipeline, buffer)

    gctx = GlobalContext(config={})
    jctx = JobContext(initial={})
    job = make_test_job(job_id="test-job")

    await buffer.put(gctx, jctx, job)

    # Allow workers to run but not retry.
    asyncio.create_task(executor.start())

    # Wait for workers to reach SlowPostStep.post_process
    await asyncio.sleep(1.0)

    # Now workers have called get() exactly once each.
    assert buffer.get_calls == 2


# ---------------------------------------------------------------------------
# OTel observability lifecycle tests
#
# Verify that `oqtopus_engine.job.process` span / metrics are finalized on
# every completion path. The pipeline marks `jctx["_oqtopus_obs_finalized"]`
# to True on the first `_finalize_job_observability` call, so asserting that
# flag is sufficient to cover the lifecycle (real span.end()/metric record
# happen inside the same function).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_obs_finalized_after_split_only():
    """Split with no following join still finalizes the parent's root span."""
    pipeline = [SplitOnPreStep()]  # no JoinStep
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx.get("_oqtopus_obs_finalized") is True


@pytest.mark.asyncio
async def test_obs_finalized_when_join_is_last_step():
    """Join at the end of the pipeline (no parent resume) still finalizes."""
    pipeline = [SplitOnPreStep(), JoinOnPostStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx.get("_oqtopus_obs_finalized") is True


@pytest.mark.asyncio
async def test_obs_finalized_on_normal_completion():
    """Sanity: a plain pipeline reaches the cursor<0 branch and finalizes."""
    pipeline: list = []  # empty pipeline → immediate end
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx.get("_oqtopus_obs_finalized") is True


@pytest.mark.asyncio
async def test_obs_ctx_propagated_to_child_jctx():
    """Children inherit the parent's `_oqtopus_obs_ctx` so workers can
    re-attach it after a Buffer transit.
    """
    pipeline = [SplitOnPreStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    parent_ctx = jctx.get("_oqtopus_obs_ctx")
    assert parent_ctx is not None
    for child_jctx in jctx.children:
        assert child_jctx.get("_oqtopus_obs_ctx") is parent_ctx


@pytest.mark.asyncio
async def test_obs_failed_when_split_child_step_fails():
    """A child step exception during a split must surface as failure on the
    root job's observability finalization, even though _safe_call swallows
    the exception and `asyncio.gather` in `_handle_split` returns normally.
    """
    pipeline = [SplitOnPreStep(), ErrorStep(), JoinOnPostStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx.get("_oqtopus_obs_finalized") is True
    assert jctx.get("_oqtopus_obs_failed") is True


@pytest.mark.asyncio
async def test_obs_failed_when_child_post_process_fails():
    """A child failure in POST_PROCESS phase must also mark the root failed."""
    pipeline = [SplitOnPreStep(), ErrorInPostStep(), JoinOnPostStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    assert jctx.get("_oqtopus_obs_finalized") is True
    assert jctx.get("_oqtopus_obs_failed") is True


# ---------------------------------------------------------------------------
# SPLIT_WITHOUT_JOIN tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_split_without_join_children_cleaned_via_cascade():
    """
    SPLIT_WITHOUT_JOIN creates children that complete without triggering join_jobs.
    cascade_cleanup removes the pending-children counter when all children finish.
    """

    class SplitWithoutJoinStep(Step):
        async def pre_process(self, gctx, jctx, job) -> StepResult:
            return StepResult()

        async def post_process(self, gctx, jctx, job) -> StepResult:
            if job.parent is not None:
                return StepResult()
            child_jobs = [make_test_job(f"{job.job_id}-child{i}") for i in range(2)]
            child_ctxs = [JobContext() for _ in range(2)]
            return StepResult(
                directive=PipelineDirective.SPLIT_WITHOUT_JOIN,
                child_jobs=child_jobs,
                child_contexts=child_ctxs,
            )

    pipeline = [SplitWithoutJoinStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext()

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, make_test_job("root")
    )

    # Children were created.
    assert len(jctx.children) == 2
    # Pending-children cleaned up via cascade_cleanup (no join triggered).
    assert executor._pending_children == {}
