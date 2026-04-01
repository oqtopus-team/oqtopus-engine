import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2
from oqtopus_engine_core.steps.device_gateway_step import DeviceGatewayStep
from oqtopus_engine_core.steps.estimator_step import INTERNAL_JOB_KEY


@pytest.fixture
def gateway_step() -> DeviceGatewayStep:
    step = DeviceGatewayStep()
    step._stub = MagicMock()
    step._stub.GetServiceStatus = AsyncMock(
        return_value=SimpleNamespace(
            service_status=qpu_pb2.ServiceStatus.SERVICE_STATUS_ACTIVE
        )
    )
    step._stub.CallJob = AsyncMock(
        return_value=SimpleNamespace(
            status=qpu_pb2.JobStatus.JOB_STATUS_SUCCESS,
            result=SimpleNamespace(counts={"00": 10}, message="ok"),
        )
    )
    return step


def _make_job(job_type: str) -> MagicMock:
    job = MagicMock()
    job.job_id = f"{job_type}-job"
    job.job_type = job_type
    job.shots = 100
    job.status = "submitted"
    job.execution_time = None
    job.job_info.transpile_result = None
    job.job_info.program = ["OPENQASM 3.0;\n"]
    job.job_info.result = None
    return job


@pytest.mark.asyncio
async def test_pre_process_internal_sampling_job_skips_repository_status_update(
    gateway_step: DeviceGatewayStep,
) -> None:
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()
    jctx = {INTERNAL_JOB_KEY: True}
    job = _make_job("sampling")

    await gateway_step.pre_process(gctx, jctx, job)

    gctx.job_repository.update_job_status_nowait.assert_not_awaited()
    gateway_step._stub.CallJob.assert_awaited_once()
    assert job.job_info.result.sampling.counts == {"00": 10}
    assert job.job_info.message == "ok"


@pytest.mark.asyncio
async def test_pre_process_internal_jobs_serialize_gateway_execution(
    gateway_step: DeviceGatewayStep,
) -> None:
    active_calls = 0
    max_active_calls = 0

    async def call_job_side_effect(request):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        return SimpleNamespace(
            status=qpu_pb2.JobStatus.JOB_STATUS_SUCCESS,
            result=SimpleNamespace(counts={"00": 10}, message=request.job_id),
        )

    gateway_step._stub.CallJob = AsyncMock(side_effect=call_job_side_effect)

    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()
    jctx = {INTERNAL_JOB_KEY: True}
    job_a = _make_job("sampling")
    job_a.job_id = "child-a"
    job_b = _make_job("sampling")
    job_b.job_id = "child-b"

    await asyncio.gather(
        gateway_step.pre_process(gctx, jctx, job_a),
        gateway_step.pre_process(gctx, jctx, job_b),
    )

    assert max_active_calls == 1
    assert job_a.job_info.message == "child-a"
    assert job_b.job_info.message == "child-b"


@pytest.mark.asyncio
async def test_pre_process_estimation_job_raises_configuration_error(
    gateway_step: DeviceGatewayStep,
) -> None:
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()
    job = _make_job("estimation")

    with pytest.raises(
        RuntimeError,
        match="estimation jobs must be split before reaching device gateway",
    ):
        await gateway_step.pre_process(gctx, {}, job)


@pytest.mark.asyncio
async def test_pre_process_internal_child_skips_repository_status_update(
    gateway_step: DeviceGatewayStep,
) -> None:
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()
    job = _make_job("sampling")

    await gateway_step.pre_process(gctx, {"has_actual_parent": True}, job)

    gctx.job_repository.update_job_status_nowait.assert_not_awaited()
    gateway_step._stub.CallJob.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_process_parent_updates_children_repository_statuses(
    gateway_step: DeviceGatewayStep,
) -> None:
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()
    child_a = _make_job("sampling")
    child_a.job_id = "child-a"
    child_b = _make_job("sampling")
    child_b.job_id = "child-b"
    parent = _make_job("sampling")
    parent.children = [child_a, child_b]

    await gateway_step.pre_process(gctx, {"has_actual_children": True}, parent)

    assert gctx.job_repository.update_job_status_nowait.await_count == 2
