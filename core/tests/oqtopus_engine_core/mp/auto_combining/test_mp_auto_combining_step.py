from unittest.mock import MagicMock

import pytest

from oqtopus_engine_core.framework import (
    Job,
    JobContext,
    JobResult,
    PipelineDirective,
    SamplingResult,
)
from oqtopus_engine_core.mp.auto_combining.mp_auto_combining_step import (
    MpAutoCombiningStep,
)


def _make_job(job_id: str, *, shots: int = 100) -> Job:
    return Job(
        job_id=job_id,
        job_type="sampling",
        device_id="device-1",
        shots=shots,
        input="",
        program=[],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
    )


def _make_combined_job() -> tuple[Job, JobContext]:
    """A combined job with 2 children and a valid divisible result."""
    combined = _make_job("mpa-comb-x")
    combined.result = JobResult(sampling=SamplingResult(counts={"00": 10, "11": 5}))
    combined.children = [_make_job("child-a"), _make_job("child-b")]

    jctx = JobContext()
    jctx.mp_auto_combining = {
        "n_total_qubits": 2,
        "combined_qubits_list": [1, 1],
    }
    jctx.children = [JobContext(), JobContext()]

    return combined, jctx


@pytest.mark.asyncio
async def test_post_process_marks_combined_job_succeeded_before_split() -> None:
    """The combined job has no job repository record of its own and never
    resumes after SPLIT_WITHOUT_JOIN (no join brings it back), so this step
    must mark it succeeded itself before returning, or it stays "ready"/
    "running" forever (Issue D).
    """
    step = MpAutoCombiningStep()
    combined, jctx = _make_combined_job()

    result = await step.post_process(MagicMock(), jctx, combined)

    assert result.directive == PipelineDirective.SPLIT_WITHOUT_JOIN
    assert combined.status == "succeeded"


@pytest.mark.asyncio
async def test_post_process_does_not_overwrite_already_failed_combined_job() -> None:
    """A combined job already marked "failed" through another path before
    reaching this point must not be flipped back to "succeeded".
    """
    step = MpAutoCombiningStep()
    combined, jctx = _make_combined_job()
    combined.status = "failed"

    result = await step.post_process(MagicMock(), jctx, combined)

    assert result.directive == PipelineDirective.SPLIT_WITHOUT_JOIN
    assert combined.status == "failed"


@pytest.mark.asyncio
async def test_post_process_skips_marking_when_not_auto_combined() -> None:
    """A job with no `mp_auto_combining` info in its context was never
    combined, so this step must leave its status untouched.
    """
    step = MpAutoCombiningStep()
    job = _make_job("plain-job")
    job.status = "ready"

    result = await step.post_process(MagicMock(), JobContext(), job)

    assert result.directive == PipelineDirective.NONE
    assert job.status == "ready"


@pytest.mark.asyncio
async def test_post_process_does_not_mark_succeeded_when_divide_fails() -> None:
    """If dividing the result raises, the combined job must not be marked
    succeeded — its own work did not actually complete.
    """
    step = MpAutoCombiningStep()
    combined, jctx = _make_combined_job()
    # A count key too short for combined_qubits_list=[1, 1] makes
    # `divide_result` raise ValueError, which this step re-raises as
    # RuntimeError.
    combined.result = JobResult(sampling=SamplingResult(counts={"0": 5}))

    with pytest.raises(RuntimeError, match="failed to extract result"):
        await step.post_process(MagicMock(), jctx, combined)

    assert combined.status == "running"
