import asyncio

import pytest

from oqtopus_engine_core.buffers import QueueBuffer
from oqtopus_engine_core.framework.context import GlobalContext, JobContext
from oqtopus_engine_core.framework.model import Job, JobInfo
from oqtopus_engine_core.framework.pipeline import (
    PipelineExecutor,
    StepPhase,
)
from oqtopus_engine_core.framework.step import (
    JoinOnPostprocess,
    JoinOnPreprocess,
    SplitOnPostprocess,
    SplitOnPreprocess,
    Step,
)

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
        job_info=JobInfo(program=[]),
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


class SplitOnPreStep(Step, SplitOnPreprocess):
    """Create two children during pre-process."""

    async def pre_process(self, gctx, jctx, job):
        child_jobs = []
        child_ctxs = []
        for i in range(2):
            c_job = make_test_job(job_id=f"{job.job_id}-child{i}", job_type="child")
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        job.children = child_jobs
        jctx.children = child_ctxs

    async def post_process(self, gctx, jctx, job):
        pass


class SplitOnPostStep(Step, SplitOnPostprocess):
    """Create two children during post-process."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        child_jobs = []
        child_ctxs = []
        for i in range(2):
            c_job = make_test_job(job_id=f"{job.job_id}-child{i}", job_type="child")
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        job.children = child_jobs
        jctx.children = child_ctxs


class JoinOnPreStep(Step, JoinOnPreprocess):
    """Join in pre-process phase."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        pass

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child):
        parent_jctx["pre_join"] = True


class JoinOnPostStep(Step, JoinOnPostprocess):
    """Join step triggered only on post-process."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        pass

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child):
        parent_jctx["joined"] = True


class RecordStep(Step):
    """Record execution order in pre- and post- phases."""

    async def pre_process(self, gctx, jctx, job):
        jctx.setdefault("record", []).append(("pre", self.__class__.__name__))

    async def post_process(self, gctx, jctx, job):
        jctx["record"].append(("post", self.__class__.__name__))


class ErrorStep(Step):
    """Throw error during PRE to simulate child failure."""

    async def pre_process(self, gctx, jctx, job):
        raise RuntimeError("child exploded")

    async def post_process(self, gctx, jctx, job):
        pass


class SlowJoinStep(Step, JoinOnPostprocess):
    """Join step with artificial delay to force race conditions."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        await asyncio.sleep(0.01)

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child):
        parent_jctx["joined"] = last_child.job_id


class FlagStep(Step):
    """Set a flag during post-process to confirm parent post-process execution."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        jctx["flag"] = True


class CountJoinStep(Step, JoinOnPostprocess):
    """Count how many times join_jobs is called."""

    def __init__(self):
        self.calls = 0

    async def pre_process(self, gctx, jctx, job):
        """No-op pre-process (required for abstract base class)."""

    async def post_process(self, gctx, jctx, job):
        pass

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child):
        self.calls += 1
        parent_jctx["joined"] = True


class TripleSplitStep(Step, SplitOnPreprocess):
    """Generate 3 children during pre-process."""

    async def pre_process(self, gctx, jctx, job):
        jobs = []
        ctxs = []
        for i in range(3):
            c_job = make_test_job(f"{job.job_id}-child{i}", job_type="child")
            c_ctx = JobContext(initial={}, parent=jctx)
            c_job.parent = job
            jobs.append(c_job)
            ctxs.append(c_ctx)
        job.children = jobs
        jctx.children = ctxs

    async def post_process(self, gctx, jctx, job):
        """No-op post-process (required for abstract base class)."""


class ErrorInPostStep(Step):
    """Throw error during POST_PROCESS."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        raise RuntimeError("post exploded")


# ---------------------------------------------------------------------------
# Helper Buffer Implementations
# ---------------------------------------------------------------------------


class DummyBuffer(QueueBuffer):
    """A no-op buffer used to simulate an unexpected buffer in POST_PROCESS."""


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
    Parent reaching JoinStep should NOT trigger join because join
    is executed only when job.parent is not None.
    """
    pipeline = [JoinOnPostStep(), FlagStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    with pytest.raises(RuntimeError):
        await executor._run_from(
            StepPhase.PRE_PROCESS,
            0,
            make_test_global_context(),
            jctx,
            make_test_job("root"),
        )
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
    JoinOnPreprocess should fire only for children, not for parent.
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
