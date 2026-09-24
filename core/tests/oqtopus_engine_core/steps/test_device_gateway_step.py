import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.framework.context import JobContext
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2
from oqtopus_engine_core.steps.device_gateway_step import (
    DeviceGatewayStep,
    _collect_status_update_targets,
)


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
    """Build a MagicMock job. Defaults to the fetcher-origin rule:
    repository_job_id == job_id, no parent, no children. Tests that need an
    estimation child or a combined job override these explicitly.
    """
    job = MagicMock()
    job.job_id = f"{job_type}-job"
    job.repository_job_id = job.job_id
    job.parent = None
    job.children = []
    job.job_type = job_type
    job.shots = 100
    job.status = "ready"
    job.execution_time = None
    job.transpile_result = None
    job.program = ["OPENQASM 3.0;\n"]
    job.result = None
    return job


@pytest.mark.asyncio
async def test_pre_process_ordinary_sampling_job_updates_its_own_status(
    gateway_step: DeviceGatewayStep,
) -> None:
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()
    jctx = JobContext()
    job = _make_job("sampling")

    await gateway_step.pre_process(gctx, jctx, job)

    gctx.job_repository.update_job_status_nowait.assert_awaited_once_with(
        job, include_output_files=False
    )
    gateway_step._stub.CallJob.assert_awaited_once()
    assert job.result.sampling.counts == {"00": 10}
    assert job.message == "ok"


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
    jctx = JobContext()
    job_a = _make_job("sampling")
    job_a.job_id = "child-a"
    job_a.repository_job_id = "child-a"
    job_b = _make_job("sampling")
    job_b.job_id = "child-b"
    job_b.repository_job_id = "child-b"

    await asyncio.gather(
        gateway_step.pre_process(gctx, jctx, job_a),
        gateway_step.pre_process(gctx, jctx, job_b),
    )

    assert max_active_calls == 2
    assert gctx.job_repository.update_job_status_nowait.await_count == 2
    assert job_a.message == "child-a"
    assert job_b.message == "child-b"


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
        await gateway_step.pre_process(gctx, JobContext(), job)


@pytest.mark.asyncio
async def test_pre_process_estimation_child_updates_parent_status(
    gateway_step: DeviceGatewayStep,
) -> None:
    """N:1, an estimation child (job_id != repository_job_id) updates its
    parent's status, not its own (it has no Cloud record of its own).
    """
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()

    parent_job = _make_job("sampling")
    parent_job.job_id = "parent-id"
    parent_job.repository_job_id = "parent-id"

    child_job = _make_job("sampling")
    child_job.job_id = "child-id"
    child_job.repository_job_id = "parent-id"  # estimation child rule
    child_job.parent = parent_job

    await gateway_step.pre_process(gctx, JobContext(), child_job)

    gctx.job_repository.update_job_status_nowait.assert_awaited_once_with(
        parent_job, include_output_files=False
    )
    gateway_step._stub.CallJob.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_process_combined_job_updates_each_childs_own_status(
    gateway_step: DeviceGatewayStep,
) -> None:
    """1:0, a combined job (sampling + mp) with no Cloud record of its own
    delegates the status update to each child, which owns its own record.
    """
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()

    combined = _make_job("sampling")
    combined.job_id = "mpa-comb-x"
    combined.repository_job_id = None

    child_a = _make_job("sampling")
    child_a.job_id = "child-a"
    child_a.repository_job_id = "child-a"
    child_a.parent = combined

    child_b = _make_job("sampling")
    child_b.job_id = "child-b"
    child_b.repository_job_id = "child-b"
    child_b.parent = combined

    combined.children = [child_a, child_b]

    await gateway_step.pre_process(gctx, JobContext(), combined)

    assert gctx.job_repository.update_job_status_nowait.await_count == 2
    updated = {
        call.args[0].job_id
        for call in gctx.job_repository.update_job_status_nowait.await_args_list
    }
    assert updated == {"child-a", "child-b"}


@pytest.mark.asyncio
async def test_pre_process_estimation_children_update_parent_status_only_once(
    gateway_step: DeviceGatewayStep,
) -> None:
    """N:1 with dedup: two estimation children processed independently
    both resolve to the same parent, but the parent is only PATCHed once
    (the second call sees status already "running" and is skipped).
    """
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()

    parent = _make_job("sampling")
    parent.job_id = "parent-job"
    parent.repository_job_id = "parent-job"

    child_a = _make_job("sampling")
    child_a.job_id = "child-a"
    child_a.repository_job_id = "parent-job"
    child_a.parent = parent

    child_b = _make_job("sampling")
    child_b.job_id = "child-b"
    child_b.repository_job_id = "parent-job"
    child_b.parent = parent

    parent.children = [child_a, child_b]

    await gateway_step.pre_process(gctx, JobContext(), child_a)
    await gateway_step.pre_process(gctx, JobContext(), child_b)

    gctx.job_repository.update_job_status_nowait.assert_awaited_once_with(
        parent, include_output_files=False
    )
    assert gateway_step._stub.CallJob.await_count == 2
    assert parent.status == "running"


# ---------------------------------------------------------------------------
# Test Cases for _collect_status_update_targets
# ---------------------------------------------------------------------------


def test_collect_status_update_targets_ordinary_job() -> None:
    job = _make_job("sampling")
    job.job_id = "lone-job"
    job.repository_job_id = "lone-job"

    targets = _collect_status_update_targets(job)

    assert len(targets) == 1
    assert targets[0].job_id == "lone-job"


def test_collect_status_update_targets_dedupes_estimation_children() -> None:
    """Two estimation children resolving to the same parent produce exactly
    one target (deduplicated by job_id), even though each is processed
    independently.
    """
    parent = _make_job("sampling")
    parent.job_id = "root"
    parent.repository_job_id = "root"

    leaf_a = _make_job("sampling")
    leaf_a.job_id = "leaf-a"
    leaf_a.repository_job_id = "root"
    leaf_a.parent = parent

    leaf_b = _make_job("sampling")
    leaf_b.job_id = "leaf-b"
    leaf_b.repository_job_id = "root"
    leaf_b.parent = parent

    parent.children = [leaf_a, leaf_b]

    targets_a = _collect_status_update_targets(leaf_a)
    targets_b = _collect_status_update_targets(leaf_b)

    assert [job.job_id for job in targets_a] == ["root"]
    assert [job.job_id for job in targets_b] == ["root"]
    assert targets_a[0] is targets_b[0]


def test_collect_status_update_targets_combined_job_spans_two_parents() -> None:
    """1:N, a combined job whose children belong to two different
    estimation parents resolves to both parents.
    """
    parent_a = _make_job("sampling")
    parent_a.job_id = "root-a"
    parent_a.repository_job_id = "root-a"

    parent_b = _make_job("sampling")
    parent_b.job_id = "root-b"
    parent_b.repository_job_id = "root-b"

    child_a = _make_job("sampling")
    child_a.job_id = "child-of-a"
    child_a.repository_job_id = "root-a"
    child_a.parent = parent_a

    child_b = _make_job("sampling")
    child_b.job_id = "child-of-b"
    child_b.repository_job_id = "root-b"
    child_b.parent = parent_b

    combined = _make_job("sampling")
    combined.job_id = "mpa-comb-x"
    combined.repository_job_id = None
    combined.children = [child_a, child_b]

    targets = _collect_status_update_targets(combined)

    target_ids = {job.job_id for job in targets}
    assert target_ids == {"root-a", "root-b"}
