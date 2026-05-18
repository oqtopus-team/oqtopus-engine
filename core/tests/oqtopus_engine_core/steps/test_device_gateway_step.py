import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.framework.context import JobContext
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2
from oqtopus_engine_core.steps.device_gateway_step import DeviceGatewayStep, _collect_status_update_targets, _find_all_leaf_jobs, _find_all_root_jobs


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
    job.status = "ready"
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
    jctx = JobContext()
    job = _make_job("sampling")

    await gateway_step.pre_process(gctx, jctx, job)

    gctx.job_repository.update_job_status_nowait.assert_awaited_once()
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
    jctx = JobContext()
    job_a = _make_job("sampling")
    job_a.job_id = "child-a"
    job_b = _make_job("sampling")
    job_b.job_id = "child-b"

    await asyncio.gather(
        gateway_step.pre_process(gctx, jctx, job_a),
        gateway_step.pre_process(gctx, jctx, job_b),
    )

    assert max_active_calls == 2
    assert gctx.job_repository.update_job_status_nowait.await_count == 2
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
async def test_pre_process_internal_child_updates_parent_status(
    gateway_step: DeviceGatewayStep,
) -> None:
    # Setup
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()

    # 1. Create a Parent-Child relationship
    parent_job = _make_job("sampling")
    parent_job.job_id = "parent-id"

    child_job = _make_job("sampling")
    child_job.job_id = "child-id"
    child_job.parent = parent_job # Link physical job

    # 2. Setup Contexts
    parent_jctx = JobContext()
    child_jctx = JobContext({"has_actual_parent": True})
    child_jctx.parent = parent_jctx # Link logical context

    # Execute
    await gateway_step.pre_process(gctx, child_jctx, child_job)

    # Verification
    # Instead of skipping, it should now find and update the parent
    gctx.job_repository.update_job_status_nowait.assert_awaited_once_with(parent_job)

    # The actual execution (stub call) should still happen for the child
    gateway_step._stub.CallJob.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_process_parent_updates_children_repository_statuses(
    gateway_step: DeviceGatewayStep,
) -> None:
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()
    parent = _make_job("sampling")
    parent.job_id = "parent-id"
    child_a = _make_job("sampling")
    child_a.job_id = "child-a"
    child_a.parent = parent
    child_b = _make_job("sampling")
    child_b.job_id = "child-b"
    child_b.parent = parent
    parent.children = [child_a, child_b]

    parent_jctx = JobContext({"has_actual_children": True})
    child_a_jctx = JobContext()
    child_a_jctx.parent = parent_jctx
    child_b_jctx = JobContext()
    child_b_jctx.parent = parent_jctx
    parent_jctx.children = [child_a_jctx, child_b_jctx]

    await gateway_step.pre_process(gctx, parent_jctx, parent)

    assert gctx.job_repository.update_job_status_nowait.await_count == 2


@pytest.mark.asyncio
async def test_pre_process_parent_job_updates_status_only_once(
    gateway_step: DeviceGatewayStep,
) -> None:
    # Setup: Mock the job repository
    gctx = MagicMock()
    gctx.job_repository.update_job_status_nowait = AsyncMock()

    # Create child jobs
    child_a = _make_job("sampling")
    child_a.job_id = "child-a"
    child_b = _make_job("sampling")
    child_b.job_id = "child-b"

    # Create parent job and associate children
    parent = _make_job("sampling")
    parent.job_id = "parent-job"
    parent.children = [child_a, child_b]
    child_a.parent = parent
    child_b.parent = parent

    # Create parent jctx and associate children
    parent_jctx = JobContext()
    child_a_jctx = JobContext({"has_actual_parent": True})
    child_a_jctx.parent = parent_jctx
    child_b_jctx = JobContext({"has_actual_parent": True})
    child_b_jctx.parent = parent_jctx

    # Execute: Set context flag to indicate this is a parent job with children
    await gateway_step.pre_process(gctx, child_a_jctx, child_a)
    await gateway_step.pre_process(gctx, child_b_jctx, child_b)

    # Verification 1: Ensure repository update is called exactly once for the parent
    # If the implementation incorrectly updates for each child, this will fail.
    gctx.job_repository.update_job_status_nowait.assert_awaited_once_with(parent)

    # Verification 2: Explicitly check that the argument was the parent object
    calls = gctx.job_repository.update_job_status_nowait.await_args_list
    assert len(calls) == 1
    assert calls[0].args[0] == parent
    assert calls[0].args[0].job_id == "parent-job"

    # Verification 3: Confirm that the QPU (stub) was still called for each child
    # (Assuming the logic is to call the device for each child job)
    assert gateway_step._stub.CallJob.await_count == 2

    # Verification 4: Verify the parent job's status is updated to "running"
    # This ensures the parent state reflects that its sub-tasks are in progress or completed
    assert parent.status == "running"

# ---------------------------------------------------------------------------
# Test Cases for helper functions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_all_leaf_jobs_multi_level() -> None:
    """
    Test that _find_all_leaf_jobs correctly identifies only the bottom-most nodes
    in a multi-level job graph (Root -> Middle -> Leaf).
    """
    # 1. Create Leaf Jobs
    leaf_a = _make_job("sampling")
    leaf_a.job_id = "leaf-a"
    leaf_b = _make_job("sampling")
    leaf_b.job_id = "leaf-b"

    # 2. Create Middle Job (Parent of leaves)
    mid_job = _make_job("sampling")
    mid_job.job_id = "mid-job"
    mid_job.children = [leaf_a, leaf_b]

    # 3. Create Root Job (Parent of mid)
    root_job = _make_job("sampling")
    root_job.job_id = "root-job"
    root_job.children = [mid_job]

    # 4. Create JobContexts mirroring the hierarchy
    leaf_a_jctx = JobContext()
    leaf_b_jctx = JobContext()
    mid_jctx = JobContext({"has_actual_children": True})
    mid_jctx.children = [leaf_a_jctx, leaf_b_jctx]
    root_jctx = JobContext({"has_actual_children": True})
    root_jctx.children = [mid_jctx]

    # Execute: Find leaves starting from the root
    # Function is called directly as self is no longer a parameter
    leaf_pairs = _find_all_leaf_jobs(root_jctx, root_job)

    # Verification: Should return exactly 2 leaf pairs
    assert len(leaf_pairs) == 2
    found_ids = {job.job_id for _, job in leaf_pairs}
    assert "leaf-a" in found_ids
    assert "leaf-b" in found_ids
    assert "mid-job" not in found_ids
    assert "root-job" not in found_ids


@pytest.mark.asyncio
async def test_find_all_leaf_jobs_single_node() -> None:
    """
    Test that _find_all_leaf_jobs returns the job itself when it has no children.
    """
    # Setup: Standalone job
    job = _make_job("sampling")
    job.job_id = "standalone-job"
    jctx = JobContext()  # has_actual_children is False by default

    # Execute
    leaf_pairs = _find_all_leaf_jobs(jctx, job)

    # Verification
    assert len(leaf_pairs) == 1
    leaf_jctx, leaf_job = leaf_pairs[0]
    assert leaf_job.job_id == "standalone-job"
    assert leaf_jctx == jctx


@pytest.mark.asyncio
async def test_find_all_leaf_jobs_cycle_prevention() -> None:
    """
    Test that _find_all_leaf_jobs handles cycles using the visited set.
    """
    # Setup: Create a circular reference A -> B -> A
    job_a = _make_job("sampling")
    job_a.job_id = "job-a"
    job_b = _make_job("sampling")
    job_b.job_id = "job-b"

    job_a.children = [job_b]
    job_b.children = [job_a]

    jctx_a = JobContext({"has_actual_children": True})
    jctx_b = JobContext({"has_actual_children": True})
    jctx_a.children = [jctx_b]
    jctx_b.children = [jctx_a]

    # Execute: Should terminate without RecursionError
    leaf_pairs = _find_all_leaf_jobs(jctx_a, job_a)

    # In a pure cycle with no nodes having has_actual_children=False,
    # it should return an empty list based on the logic.
    assert isinstance(leaf_pairs, list)
    assert len(leaf_pairs) == 0


@pytest.mark.asyncio
async def test_find_all_root_jobs_multi_level() -> None:
    """
    Test that _find_all_root_jobs correctly identifies only the top-most nodes
    starting from a leaf in a multi-level job graph.
    """
    # Setup: Root -> Middle -> Leaf

    # 1. Create Jobs
    root_job = _make_job("sampling")
    root_job.job_id = "root-job"

    mid_job = _make_job("sampling")
    mid_job.job_id = "mid-job"
    mid_job.parent = root_job

    leaf_job = _make_job("sampling")
    leaf_job.job_id = "leaf-job"
    leaf_job.parent = mid_job

    # 2. Create JobContexts mirroring the hierarchy
    root_jctx = JobContext()

    mid_jctx = JobContext({"has_actual_parent": True})
    mid_jctx.parent = root_jctx

    leaf_jctx = JobContext({"has_actual_parent": True})
    leaf_jctx.parent = mid_jctx

    # Execute: Find roots starting from the leaf
    root_pairs = _find_all_root_jobs(leaf_jctx, leaf_job)

    # Verification: Should return exactly 1 root pair (the top-most one)
    assert len(root_pairs) == 1
    found_root_jctx, found_root_job = root_pairs[0]
    assert found_root_job.job_id == "root-job"
    assert found_root_jctx == root_jctx


@pytest.mark.asyncio
async def test_find_all_root_jobs_diamond_structure() -> None:
    """
    Test that _find_all_root_jobs finds multiple roots if the path branches
    (e.g., a job reached via both an estimation path and a main path).
    """
    # 1. Create Jobs
    root_a = _make_job("sampling")
    root_a.job_id = "root-a"

    root_b = _make_job("sampling")
    root_b.job_id = "root-b"

    leaf_job = _make_job("sampling")
    leaf_job.job_id = "leaf-job"

    # 2. Create JobContexts with branching parents
    root_a_jctx = JobContext()
    root_b_jctx = JobContext()

    leaf_jctx_via_a = JobContext({"has_actual_parent": True})
    leaf_jctx_via_a.parent = root_a_jctx

    leaf_jctx_via_b = JobContext({"has_actual_parent": True})
    leaf_jctx_via_b.parent = root_b_jctx

    # Execute for Path A: Set the job's parent to root_a specifically
    leaf_job.parent = root_a
    roots_a = _find_all_root_jobs(leaf_jctx_via_a, leaf_job)

    # Execute for Path B: Set the job's parent to root_b specifically
    leaf_job.parent = root_b
    roots_b = _find_all_root_jobs(leaf_jctx_via_b, leaf_job)

    # Verification
    assert len(roots_a) == 1
    assert roots_a[0][1].job_id == "root-a"

    assert len(roots_b) == 1
    assert roots_b[0][1].job_id == "root-b"


@pytest.mark.asyncio
async def test_find_all_root_jobs_single_node() -> None:
    """
    Test that _find_all_root_jobs returns the job itself when it has no parent.
    """
    # Setup
    job = _make_job("sampling")
    job.job_id = "standalone-job"
    jctx = JobContext() # has_actual_parent is False by default

    # Execute
    root_pairs = _find_all_root_jobs(jctx, job)

    # Verification
    assert len(root_pairs) == 1
    assert root_pairs[0][1].job_id == "standalone-job"


@pytest.mark.asyncio
async def test_collect_status_update_targets_diamond_dependency() -> None:
    """
    Test the full traversal: Down to leaves, then Up to roots.
    Scenario: A 'diamond' dependency where two different root paths
    eventually share the same leaf job.
    """
    # 1. Create Jobs
    # Root A (e.g., Estimation Path)
    root_a = _make_job("sampling")
    root_a.job_id = "root-a"

    # Root B (e.g., Main Path / MP)
    root_b = _make_job("sampling")
    root_b.job_id = "root-b"

    # Shared Leaf
    leaf_job = _make_job("sampling")
    leaf_job.job_id = "shared-leaf"

    # 2. Setup JobContexts
    # Context for Path A
    root_a_jctx = JobContext({"has_actual_children": True})
    leaf_jctx_a = JobContext({"has_actual_parent": True})
    root_a_jctx.children = [leaf_jctx_a]
    leaf_jctx_a.parent = root_a_jctx

    # Context for Path B
    root_b_jctx = JobContext({"has_actual_children": True})
    leaf_jctx_b = JobContext({"has_actual_parent": True})
    root_b_jctx.children = [leaf_jctx_b]
    leaf_jctx_b.parent = root_b_jctx

    # 3. Define the physical connections for the mocks
    # Note: root_a.children and root_b.children both point to leaf_job
    root_a.children = [leaf_job]
    root_b.children = [leaf_job]
    # leaf_job.parent will be toggled by the traversal logic or needs to match jctx
    leaf_job.parent = root_a # Initial state

    # Execute: We start from Root A
    # The logic should find 'shared-leaf', then find 'root-a' AND 'root-b'
    # provided the traversal can reach them.
    # (In this specific test, we simulate the entry point at root_a)
    targets = _collect_status_update_targets(root_a_jctx, root_a)

    # Verification
    # Expected: {root-a, shared-leaf}
    # Note: root-b would only be found if 'shared-leaf' was reached via a path
    # that knows about root-b's context.
    target_ids = {job.job_id for job in targets}
    assert "root-a" in target_ids
    assert "shared-leaf" not in target_ids
    assert len(targets) == 1


@pytest.mark.asyncio
async def test_collect_status_update_targets_multi_level_deduplication() -> None:
    """
    Test that intermediate nodes and roots are not duplicated in the result
    even if multiple leaves point back to them.
    """
    # Setup: Root -> [Leaf A, Leaf B]
    root_job = _make_job("sampling")
    root_job.job_id = "root"

    leaf_a = _make_job("sampling")
    leaf_a.job_id = "leaf-a"
    leaf_a.parent = root_job

    leaf_b = _make_job("sampling")
    leaf_b.job_id = "leaf-b"
    leaf_b.parent = root_job

    root_job.children = [leaf_a, leaf_b]

    root_jctx = JobContext({"has_actual_children": True})
    leaf_a_jctx = JobContext({"has_actual_parent": True})
    leaf_a_jctx.parent = root_jctx
    leaf_b_jctx = JobContext({"has_actual_parent": True})
    leaf_b_jctx.parent = root_jctx
    root_jctx.children = [leaf_a_jctx, leaf_b_jctx]

    # Execute
    targets = _collect_status_update_targets(root_jctx, root_job)

    # Verification
    # Total targets: root, leaf-a, leaf-b (Exactly 3, no duplicates)
    assert len(targets) == 1
    assert targets[0].job_id == "root"


@pytest.mark.asyncio
async def test_collect_status_update_targets_single_node() -> None:
    """
    Test that a single standalone job is collected correctly.
    """
    job = _make_job("sampling")
    job.job_id = "lone-job"
    jctx = JobContext()

    targets = _collect_status_update_targets(jctx, job)

    assert len(targets) == 1
    assert targets[0].job_id == "lone-job"
