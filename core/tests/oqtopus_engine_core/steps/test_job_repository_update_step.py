from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.framework import Job, JobContext, JobInfo
from oqtopus_engine_core.steps import JobRepositoryUpdateStep


def _build_job(execution_time: float | None = None) -> Job:
    return Job(
        job_id="job-1",
        device_id="device-1",
        shots=100,
        job_type="estimation",
        job_info=JobInfo(program=["OPENQASM 3.0; include \"stdgates.inc\";"]),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
        execution_time=execution_time,
    )


@pytest.mark.asyncio
async def test_post_process_keeps_none_execution_time_when_not_set() -> None:
    step = JobRepositoryUpdateStep()
    gctx = SimpleNamespace(job_repository=SimpleNamespace(update_job_info_nowait=AsyncMock()))
    jctx = JobContext()
    job = _build_job(execution_time=None)

    await step.post_process(gctx, jctx, job)

    assert job.status == "succeeded"
    assert job.execution_time is None
    gctx.job_repository.update_job_info_nowait.assert_awaited_once_with(
        job, overwrite_status="succeeded", execution_time=None
    )


@pytest.mark.asyncio
async def test_post_process_keeps_existing_execution_time() -> None:
    step = JobRepositoryUpdateStep()
    gctx = SimpleNamespace(job_repository=SimpleNamespace(update_job_info_nowait=AsyncMock()))
    jctx = JobContext()
    job = _build_job(execution_time=0.789)

    await step.post_process(gctx, jctx, job)

    assert job.status == "succeeded"
    assert job.execution_time == 0.789
    gctx.job_repository.update_job_info_nowait.assert_awaited_once_with(
        job, overwrite_status="succeeded", execution_time=0.789
    )
