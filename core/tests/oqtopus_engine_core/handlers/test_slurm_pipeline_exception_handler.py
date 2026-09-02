import pytest

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext
from oqtopus_engine_core.handlers import SlurmPipelineExceptionHandler
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.slurm import ExecutionState
from oqtopus_engine_core.steps import (
    SlurmCancellationPendingError,
    SlurmJobCancelledError,
)
from ..slurm.in_memory_execution_repository import (
    InMemorySlurmExecutionRepository as SlurmExecutionRepository,
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
    execution_repository = SlurmExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    return (
        SlurmPipelineExceptionHandler(execution_repository, repository),
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
        SlurmJobCancelledError("SLURM cancellation confirmed"),
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
        SlurmJobCancelledError("SLURM allocation was cancelled"),
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.updated_statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.CANCELLED
    assert record.cloud_status == "cancelled"
