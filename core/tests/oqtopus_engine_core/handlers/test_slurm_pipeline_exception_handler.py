import hashlib
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext
from oqtopus_engine_core.handlers import SlurmPipelineExceptionHandler
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.simulator import (
    ExecutionState,
    SchedulerJobStatus,
    SchedulerState,
    SlurmSubmissionUncertainError,
)
from oqtopus_engine_core.steps import (
    SlurmCancellationPendingError,
    JobCancelledError,
)
from ..simulator.execution.in_memory_execution_repository import (
    InMemoryExecutionRepository as ExecutionRepository,
)


def make_job(status: str = "running") -> Job:
    return Job(
        job_id="job-1",
        device_id="large-simulator",
        shots=10,
        job_type="sampling",
        input="input.zip",
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status=status,
    )


class RecordingJobRepository(NullJobRepository):
    def __init__(self, cloud_status: str):
        super().__init__()
        self.cloud_status = cloud_status
        self.updated_statuses: list[str] = []

    async def get_job(self, job_id: str):
        return make_job(self.cloud_status)

    async def update_job_status(self, job: Job):
        self.updated_statuses.append(job.status)


async def make_handler(tmp_path, repository: RecordingJobRepository):
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    return (
        SlurmPipelineExceptionHandler(
            execution_repository, repository, str(tmp_path / "work"), AsyncMock()
        ),
        execution_repository,
    )


@pytest.mark.asyncio
async def test_handler_preserves_cloud_cancellation(tmp_path):
    repository = RecordingJobRepository("cancelled")
    handler, execution_repository = await make_handler(tmp_path, repository)

    await handler.handle_exception(
        RuntimeError("pipeline stopped"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.CANCELLED


@pytest.mark.asyncio
async def test_handler_preserves_pending_slurm_cancellation(tmp_path):
    repository = RecordingJobRepository("cancelled")
    handler, execution_repository = await make_handler(tmp_path, repository)
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
        cloud_status="cancelled",
    )
    await handler.handle_exception(
        SlurmCancellationPendingError("controller unavailable"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RUNNING
    assert record.cloud_status == "cancelled"


@pytest.mark.asyncio
async def test_handler_preserves_derived_cancelling_condition(tmp_path):
    repository = RecordingJobRepository("cancelling")
    handler, execution_repository = await make_handler(tmp_path, repository)
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
        cloud_status="cancelling",
    )
    await handler.handle_exception(
        SlurmCancellationPendingError("controller unavailable"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RUNNING


@pytest.mark.asyncio
async def test_handler_syncs_confirmed_requested_cancellation(tmp_path):
    repository = RecordingJobRepository("running")
    handler, execution_repository = await make_handler(tmp_path, repository)
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
        cloud_status="running",
    )
    await handler.handle_exception(
        JobCancelledError("SLURM cancellation confirmed"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.CANCELLED
    assert record.cloud_status == "cancelled"


@pytest.mark.asyncio
async def test_handler_marks_non_cancel_failure(tmp_path):
    repository = RecordingJobRepository("running")
    handler, execution_repository = await make_handler(tmp_path, repository)

    await handler.handle_exception(
        RuntimeError("SLURM failed"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.FAILED
    assert record.last_error == "SLURM failed"


@pytest.mark.asyncio
async def test_handler_preserves_result_ready_for_finalize_retry(tmp_path):
    repository = RecordingJobRepository("running")
    handler, execution_repository = await make_handler(tmp_path, repository)
    await execution_repository.update(
        "job-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.READY},
    )
    await handler.handle_exception(
        RuntimeError("Cloud upload unavailable"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY
    assert record.last_error == "Cloud upload unavailable"


@pytest.mark.asyncio
async def test_handler_preserves_failed_diagnostic_state(tmp_path):
    repository = RecordingJobRepository("running")
    handler, execution_repository = await make_handler(tmp_path, repository)
    await execution_repository.update(
        "job-1",
        ExecutionState.FAILED,
        expected={ExecutionState.READY},
        last_error="allocation disappeared",
    )
    await handler.handle_exception(
        RuntimeError("allocation disappeared"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.FAILED
    assert record.cloud_status == "failed"


@pytest.mark.asyncio
async def test_handler_syncs_external_slurm_cancel_to_cloud(tmp_path):
    repository = RecordingJobRepository("running")
    handler, execution_repository = await make_handler(tmp_path, repository)
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
        cloud_status="running",
    )
    await handler.handle_exception(
        JobCancelledError("SLURM allocation was cancelled"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.CANCELLED
    assert record.cloud_status == "cancelled"


@pytest.mark.asyncio
async def test_handler_applies_internal_child_failure_to_root(tmp_path):
    repository = RecordingJobRepository("running")
    handler, execution_repository = await make_handler(tmp_path, repository)
    parent = make_job()
    child = make_job()
    child.job_id = "job-1-estimation-0"
    child.parent = parent

    await handler.handle_exception(
        RuntimeError("child allocation failed"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        child,
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get(parent.job_id)
    assert record is not None
    assert record.state is ExecutionState.FAILED
    assert record.cloud_status == "failed"
    assert record.last_error == "child allocation failed"


async def prepare_child(handler, parent, child_id, state):
    child = make_job()
    child.job_id = child_id
    child.parent = parent
    parent.children.append(child)
    repository = handler._internal_execution_repository
    await repository.claim(child_id, "sampling", parent_job_id=parent.job_id)
    work_dir = handler._work_root / hashlib.sha256(child_id.encode()).hexdigest()[:32]
    work_dir.mkdir()
    (work_dir / "request.json").write_text("request")
    await repository.prepare(
        cloud_job_id=child_id,
        request_hash="digest",
        options={},
        work_dir=work_dir,
        request_path=work_dir / "request.json",
        result_path=work_dir / "result.json",
    )
    await repository.update(child_id, state, slurm_job_id="12345")
    return child, work_dir


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_child_failure_cleans_confirmed_child_and_completed_sibling(
    tmp_path, cancelled
):
    cloud = RecordingJobRepository("running")
    handler, root_records = await make_handler(tmp_path, cloud)
    parent = make_job()
    child, work = await prepare_child(
        handler, parent, "failed-child", ExecutionState.RUNNING
    )
    done, done_work = await prepare_child(
        handler, parent, "done-child", ExecutionState.SUCCEEDED
    )
    active, active_work = await prepare_child(
        handler, parent, "active-child", ExecutionState.RUNNING
    )
    state = SchedulerState.CANCELLED if cancelled else SchedulerState.FAILED
    handler._slurm_client.get_status.return_value = SchedulerJobStatus(
        "12345", state, state.name
    )
    error = JobCancelledError("cancelled") if cancelled else RuntimeError("failed")

    await handler.handle_exception(
        error, GlobalContext(config={}, job_repository=cloud), JobContext(), child
    )

    assert (await root_records.get(parent.job_id)).state is (
        ExecutionState.CANCELLED if cancelled else ExecutionState.FAILED
    )
    assert not work.exists() and not done_work.exists()
    local = handler._internal_execution_repository
    assert await local.get(child.job_id) is None
    assert await local.get(done.job_id) is None
    assert active_work.exists()
    assert (await local.get(active.job_id)).state is ExecutionState.RUNNING
    handler._slurm_client.get_status.assert_awaited_once_with("12345")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [ExecutionState.READY, ExecutionState.RESULT_READY])
async def test_child_failure_before_submit_or_after_result_cleans_locally(
    tmp_path, state
):
    cloud = RecordingJobRepository("running")
    handler, _ = await make_handler(tmp_path, cloud)
    child, work = await prepare_child(handler, make_job(), "child", state)

    await handler.handle_exception(
        RuntimeError("pipeline failed"),
        GlobalContext(config={}, job_repository=cloud),
        JobContext(),
        child,
    )

    assert not work.exists()
    assert await handler._internal_execution_repository.get(child.job_id) is None
    handler._slurm_client.get_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [SlurmSubmissionUncertainError, SlurmCancellationPendingError]
)
async def test_unresolved_child_keeps_parent_and_artifacts_recoverable(
    tmp_path, error_type
):
    cloud = RecordingJobRepository("running")
    handler, root_records = await make_handler(tmp_path, cloud)
    await root_records.update("job-1", ExecutionState.RUNNING)
    child, work = await prepare_child(
        handler, make_job(), "child", ExecutionState.RUNNING
    )

    await handler.handle_exception(
        error_type("unknown"),
        GlobalContext(config={}, job_repository=cloud),
        JobContext(),
        child,
    )

    assert (await root_records.get("job-1")).state is ExecutionState.RUNNING
    assert (
        await handler._internal_execution_repository.get(child.job_id)
    ).state is ExecutionState.RUNNING
    assert work.exists()
    handler._slurm_client.get_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observation",
    [
        None,
        SchedulerState.RUNNING,
        SchedulerState.UNKNOWN,
        TimeoutError("scheduler unavailable"),
    ],
)
async def test_child_cleanup_preserves_unconfirmed_allocation(tmp_path, observation):
    cloud = RecordingJobRepository("running")
    handler, root_records = await make_handler(tmp_path, cloud)
    child, work = await prepare_child(
        handler, make_job(), "child", ExecutionState.RUNNING
    )
    if isinstance(observation, Exception):
        handler._slurm_client.get_status.side_effect = observation
    else:
        handler._slurm_client.get_status.return_value = (
            SchedulerJobStatus("12345", observation, observation.name)
            if observation
            else None
        )

    await handler.handle_exception(
        RuntimeError("pipeline failed"),
        GlobalContext(config={}, job_repository=cloud),
        JobContext(),
        child,
    )

    assert (await root_records.get("job-1")).state is ExecutionState.FAILED
    assert (
        await handler._internal_execution_repository.get(child.job_id)
    ).state is ExecutionState.RUNNING
    assert work.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_block_parent_status_or_other_children(
    tmp_path, monkeypatch
):
    cloud = RecordingJobRepository("running")
    handler, root_records = await make_handler(tmp_path, cloud)
    parent = make_job()
    child, work = await prepare_child(
        handler, parent, "child", ExecutionState.RESULT_READY
    )
    sibling, sibling_work = await prepare_child(
        handler, parent, "done", ExecutionState.SUCCEEDED
    )
    repository = handler._internal_execution_repository
    cleanup = repository.cleanup_artifacts

    async def fail_one(job_id, root):
        if job_id == child.job_id:
            raise PermissionError("read-only directory")
        return await cleanup(job_id, root)

    monkeypatch.setattr(repository, "cleanup_artifacts", fail_one)
    await handler.handle_exception(
        RuntimeError("join failed"),
        GlobalContext(config={}, job_repository=cloud),
        JobContext(),
        child,
    )

    assert (await root_records.get(parent.job_id)).state is ExecutionState.FAILED
    assert (await repository.get(child.job_id)).state is ExecutionState.FAILED
    assert work.exists() and not sibling_work.exists()
    assert await repository.get(sibling.job_id) is None


@pytest.mark.asyncio
async def test_root_join_failure_cleans_successful_children(tmp_path):
    cloud = RecordingJobRepository("running")
    handler, _ = await make_handler(tmp_path, cloud)
    parent = make_job()
    child, work = await prepare_child(handler, parent, "done", ExecutionState.SUCCEEDED)

    await handler.handle_exception(
        RuntimeError("estimator join failed"),
        GlobalContext(config={}, job_repository=cloud),
        JobContext(),
        parent,
    )

    assert not work.exists()
    assert await handler._internal_execution_repository.get(child.job_id) is None
