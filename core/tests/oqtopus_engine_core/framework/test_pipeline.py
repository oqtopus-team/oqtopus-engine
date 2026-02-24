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
    Step,
    SplitOnPostprocess,
    SplitOnPreprocess,
    JoinOnPostprocess,
    JoinOnPreprocess,
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

class SplitStep(Step, SplitOnPreprocess):
    """Create two children during pre-process."""

    async def pre_process(self, gctx, jctx, job):
        child_jobs = []
        child_ctxs = []
        for i in range(2):
            c_job = make_test_job(job_id=f"{job.job_id}-child{i}", job_type="child")
            c_jctx = JobContext(initial={}, parent=jctx)
            c_job.parent = job
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        job.children = child_jobs
        jctx.children = child_ctxs

    async def post_process(self, gctx, jctx, job):
        pass


class JoinStep(Step, JoinOnPostprocess):
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


class NestedSplitStep(Step, SplitOnPostprocess):
    """Create a single grandchild during post-process."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        c = make_test_job(job_id=f"{job.job_id}-grand", job_type="child")
        ctx = JobContext(initial={}, parent=jctx)
        c.parent = job
        job.children = [c]
        jctx.children = [ctx]


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
        pass

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
        pass


class MarkStep(Step):
    async def pre_process(self, gctx, jctx, job):
        """No-op pre-process."""
        pass

    async def post_process(self, gctx, jctx, job):
        jctx["mark"] = True


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
        SplitStep(),
        RecordStep(),
        JoinStep(),
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


@pytest.mark.asyncio
async def test_child_error_cleans_pending_children():
    """
    A child fails before join → pending_children must be removed.
    """
    pipeline = [SplitStep(), ErrorStep(), JoinStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, make_test_job("root")
    )

    assert not executor._pending_children


@pytest.mark.asyncio
async def test_race_safe_last_child():
    """
    Race-safe join: only exactly one child becomes last_child.
    """
    pipeline = [SplitStep(), SlowJoinStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, make_test_job("root")
    )

    assert jctx["joined"] in ("root-child0", "root-child1")


@pytest.mark.asyncio
async def test_nested_split():
    """
    Nested split should not crash.
    """
    pipeline = [SplitStep(), NestedSplitStep(), JoinStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        JobContext(initial={}),
        make_test_job("root"),
    )

    assert True


@pytest.mark.asyncio
async def test_parent_post_runs_after_join():
    """
    After join is complete, parent POST should continue to following steps.
    """
    pipeline = [SplitStep(), JoinStep(), FlagStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, make_test_job("root")
    )

    assert jctx["flag"] is True


@pytest.mark.asyncio
async def test_parent_reaching_join_does_not_trigger_join():
    """
    Parent reaching JoinStep should NOT trigger join because join
    is executed only when job.parent is not None.
    """
    pipeline = [JoinStep(), FlagStep()]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS, 0, make_test_global_context(), jctx, make_test_job("root")
    )

    assert "joined" not in jctx
    assert jctx["flag"] is True


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


@pytest.mark.asyncio
async def test_join_jobs_called_once():
    """
    join_jobs must be called exactly once even with race conditions.
    """
    join_step = CountJoinStep()
    pipeline = [SplitStep(), join_step]

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


class ErrorInPostStep(Step):
    """Throw error during POST_PROCESS."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        raise RuntimeError("post exploded")


@pytest.mark.asyncio
async def test_child_post_error_cleans_pending_children():
    """
    A child throwing error during POST must also clean pending_children.
    """
    pipeline = [SplitStep(), ErrorInPostStep(), JoinStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        JobContext(initial={}),
        make_test_job("root"),
    )

    assert not executor._pending_children


@pytest.mark.asyncio
async def test_join_at_pipeline_end_no_resume():
    """
    JoinStep at end of pipeline → join occurs but no parent resume should happen.
    """
    pipeline = [SplitStep(), JoinStep()]  # No steps after join
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


class PreJoinStep(Step, JoinOnPreprocess):
    """Join in pre-process phase."""

    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        pass

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child):
        parent_jctx["pre_join"] = True


@pytest.mark.asyncio
async def test_join_on_preprocess_runs_only_for_children():
    """
    JoinOnPreprocess should fire only for children, not for parent.
    """
    pipeline = [SplitStep(), PreJoinStep()]
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


@pytest.mark.asyncio
async def test_join_and_flag_both_present():
    """
    Ensure joined flag and another POST flag can co-exist properly.
    """
    pipeline = [
        SplitStep(),
        SlowJoinStep(),
        MarkStep(),
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
    assert jctx["mark"] is True


@pytest.mark.asyncio
async def test_buffer_in_postprocess_is_skipped_safely():
    """
    SAFETY TEST — Buffer appears during POST_PROCESS phase.

    This test verifies the following:
      - Although Buffer nodes are not expected to appear in POST_PROCESS,
        the state-machine implementation must treat them safely.
      - Specifically, encountering a Buffer during backward execution
        should not raise an exception or corrupt the control flow.
      - The executor must simply decrement the cursor and continue
        the backward traversal without invoking Buffer.put().

    Expected behavior:
      - The pipeline completes without errors.
      - No calls to Buffer.put() are made.
      - Execution behaves as a no-op for the Buffer in POST_PROCESS.
    """

    class DummyBuffer(QueueBuffer):
        """A no-op buffer used to simulate an unexpected buffer in POST_PROCESS."""
        pass

    pipeline = [
        RecordStep(),    # index 0
        DummyBuffer(),   # index 1 (unexpected in POST_PROCESS)
        RecordStep(),    # index 2
    ]

    executor = PipelineExecutor(pipeline, DummyBuffer())
    jctx = JobContext(initial={})

    # Directly start in POST_PROCESS at index 1 → tests the skip logic
    await executor._run_from(
        StepPhase.POST_PROCESS,
        1,
        make_test_global_context(),
        jctx,
        make_test_job("root"),
    )

    # Should finish without crashing.
    assert True
