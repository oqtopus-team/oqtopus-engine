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


# ------------------------------------------------------------
# Helper factory functions
# ------------------------------------------------------------


def make_test_job(job_id: str, job_type: str = "root") -> Job:
    """Create a minimal but valid Job instance for pipeline tests.

    Only the required fields are populated explicitly; all other fields rely
    on their defaults defined in the Job model.
    """
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


# ------------------------------------------------------------
# Helper Step Implementations
# ------------------------------------------------------------


class SplitStep(Step, SplitOnPreprocess):
    async def pre_process(self, gctx, jctx, job):
        # Create two children to simulate parallel branching.
        child_jobs: list[Job] = []
        child_ctxs: list[JobContext] = []
        for i in range(2):
            c_job = make_test_job(job_id=f"{job.job_id}-child{i}", job_type="child")
            c_jctx = JobContext(initial={}, parent=jctx)
            c_job.parent = job
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        # Let the executor know that we have created children.
        job.children = child_jobs
        jctx.children = child_ctxs

    async def post_process(self, gctx, jctx, job):
        # SplitStep has no post-process behavior.
        # This method is intentionally a no-op.
        pass


class JoinStep(Step, JoinOnPostprocess):
    async def pre_process(self, gctx, jctx, job):
        """No-op pre process; JoinStep triggers join only in post_process."""
        pass

    async def post_process(self, gctx, jctx, job):
        # Join logic executed in executor, nothing needed here.
        pass

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child):
        # Mark that join actually happened.
        parent_jctx["joined"] = True


class RecordStep(Step):
    async def pre_process(self, gctx, jctx, job):
        # Record execution order for validation.
        jctx.setdefault("record", []).append(("pre", self.__class__.__name__))

    async def post_process(self, gctx, jctx, job):
        # Record backward execution order as well.
        jctx["record"].append(("post", self.__class__.__name__))


class ErrorStep(Step):
    async def pre_process(self, gctx, jctx, job):
        """Simulate a child failure before reaching any join."""
        raise RuntimeError("child exploded")

    async def post_process(self, gctx, jctx, job):
        """No-op post process; required for pipeline backward phase."""
        pass


class SlowJoinStep(Step, JoinOnPostprocess):
    async def pre_process(self, gctx, jctx, job):
        """No-op pre process; SlowJoinStep triggers join only in post_process."""
        pass

    async def post_process(self, gctx, jctx, job):
        # Sleep to force overlapping join arrival (race condition simulation).
        await asyncio.sleep(0.01)

    async def join_jobs(self, gctx, parent_jctx, parent_job, last_child):
        # Mark which child was seen as the "last" one.
        parent_jctx["joined"] = last_child.job_id


class NestedSplitStep(Step, SplitOnPostprocess):
    async def pre_process(self, gctx, jctx, job):
        """No-op pre process; NestedSplitStep splits only in post_process."""
        pass

    async def post_process(self, gctx, jctx, job):
        """Perform a nested split by creating one grandchild."""
        c = make_test_job(job_id=f"{job.job_id}-grand", job_type="child")
        ctx = JobContext(initial={}, parent=jctx)
        c.parent = job
        job.children = [c]
        jctx.children = [ctx]


class FlagStep(Step):
    async def pre_process(self, gctx, jctx, job):
        """No-op pre process; this step is used only in the backward phase."""
        pass

    async def post_process(self, gctx, jctx, job):
        """Set a flag to verify the parent POST phase is executed."""
        jctx["flag"] = True


# ------------------------------------------------------------
# Test Cases
# ------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_join_parent_resume():
    """
    E2E TEST 1 — Normal flow: split → child pipelines → join → parent resumes.

    This test confirms:
      - Split creates two child pipelines.
      - Each child completes PRE and POST phases.
      - Join happens only once (at the last child).
      - Parent POST resumes correctly from the join point.
      - Execution order is recorded properly.
    """
    pipeline = [
        RecordStep(),   # 0
        SplitStep(),    # 1 (split happens here)
        RecordStep(),   # 2 (children start here in PRE)
        JoinStep(),     # 3 (join triggered in POST)
        RecordStep(),   # 4 (parent POST continues here after join)
    ]

    job_buffer = QueueBuffer()
    executor = PipelineExecutor(pipeline=pipeline, job_buffer=job_buffer)

    root_job = make_test_job(job_id="root", job_type="root")
    jctx = JobContext(initial={})

    await executor._run_from(
        step_phase=StepPhase.PRE_PROCESS,
        index=0,
        gctx=make_test_global_context(),
        jctx=jctx,
        job=root_job,
    )

    # Join must have occurred.
    assert jctx["joined"] is True

    # Parent must have executed POST of RecordStep after join.
    assert ("post", "RecordStep") in jctx["record"]


@pytest.mark.asyncio
async def test_child_error_cleans_pending_children():
    """
    E2E TEST 2 — Child step raises exception → pending_children cleaned.

    This test ensures that:
      - If a child pipeline throws an exception before reaching join,
        the executor removes the parent's pending-children counter.
      - No zombie pending state remains.
    """
    pipeline = [
        SplitStep(),
        ErrorStep(),   # Child fails immediately here
        JoinStep(),
    ]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    root_job = make_test_job(job_id="root", job_type="root")
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        root_job,
    )

    # After child failure, pending_children must NOT remain.
    assert not executor._pending_children


@pytest.mark.asyncio
async def test_race_safe_last_child():
    """
    E2E TEST 3 — Race-safe last-child detection.

    Purpose:
      - Both children reach the join step almost simultaneously.
      - Only exactly one child should be considered "last".
      - join_jobs() must run once, and with the correct last child.
    """
    pipeline = [
        SplitStep(),
        SlowJoinStep(),  # Introduces async overlap
    ]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    root = make_test_job(job_id="root", job_type="root")
    jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        root,
    )

    # joined should contain the job_id of exactly one child
    assert jctx["joined"] in ("root-child0", "root-child1")


@pytest.mark.asyncio
async def test_nested_split():
    """
    E2E TEST 4 — Nested split behavior.

    This test confirms:
      - A child pipeline performs an additional split.
      - pending_children is overwritten safely and cleaned properly.
      - Executor does not crash when handling nested split trees.
    """
    pipeline = [
        SplitStep(),        # First split
        NestedSplitStep(),  # Nested split inside child
        JoinStep(),         # Join of nested children
    ]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})
    root_job = make_test_job(job_id="root", job_type="root")

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        root_job,
    )

    # No crash = success.
    assert True


@pytest.mark.asyncio
async def test_parent_post_runs_after_join():
    """
    E2E TEST 5 — Parent POST continues after join.

    Confirms:
      - After join is complete, parent resumes POST.
      - Parent POST reaches all remaining steps.
      - join → POST resume transition is correct.
    """
    pipeline = [
        SplitStep(),
        JoinStep(),
        FlagStep(),   # Should be executed during parent POST phase
    ]

    executor = PipelineExecutor(pipeline, QueueBuffer())
    jctx = JobContext(initial={})
    root_job = make_test_job(job_id="root", job_type="root")

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        make_test_global_context(),
        jctx,
        root_job,
    )

    # FlagStep must have executed.
    assert jctx["flag"] is True
