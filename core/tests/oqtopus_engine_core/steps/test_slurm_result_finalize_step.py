import pytest

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobResult,
    SamplingResult,
)
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.slurm import ExecutionState
from oqtopus_engine_core.steps import SlurmResultFinalizeStep

from ..slurm.in_memory_execution_repository import (
    InMemorySlurmExecutionRepository as SlurmExecutionRepository,
)


def make_job(status: str = "running") -> Job:
    return Job(
        job_id="job-1",
        device_id="large-simulator",
        shots=10,
        job_type="sampling",
        input="https://example.invalid/input.zip",
        result=JobResult(sampling=SamplingResult(counts={"1": 10})),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status=status,
    )


class RecordingJobRepository(NullJobRepository):
    def __init__(
        self,
        cloud_statuses: list[str] | None = None,
        upload_errors: list[Exception] | None = None,
    ):
        super().__init__()
        self.cloud_statuses = cloud_statuses or ["running"]
        self.upload_errors = upload_errors or []
        self.uploads = []
        self.statuses = []

    async def get_job(self, job_id: str):
        status = (
            self.cloud_statuses.pop(0)
            if len(self.cloud_statuses) > 1
            else self.cloud_statuses[0]
        )
        return make_job(status)

    async def upload_job_outputs(self, job, outputs):
        self.uploads.append(outputs)
        if self.upload_errors:
            raise self.upload_errors.pop(0)

    async def update_job_status(self, job):
        self.statuses.append(job.status)


@pytest.mark.asyncio
async def test_finalizer_closes_metadata_with_cloud_update(tmp_path):
    execution_repository = SlurmExecutionRepository(tmp_path / "repository-placeholder")
    work_root = tmp_path / "work"
    work_dir = work_root / "job-token"
    work_dir.mkdir(parents=True)
    request_path = work_dir / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.prepare(
        cloud_job_id="job-1",
        request_hash="digest",
        options={},
        work_dir=work_dir,
        request_path=request_path,
        result_path=work_dir / "result.json",
    )
    await execution_repository.update(
        "job-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository()
    job = make_job()

    await SlurmResultFinalizeStep(
        execution_repository,
        repository,
        str(work_root),
    ).post_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        job,
    )

    assert len(repository.uploads) == 1
    assert repository.statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.SUCCEEDED
    assert record.work_dir is None
    assert not work_dir.exists()


@pytest.mark.asyncio
async def test_result_ready_wins_over_later_cancellation_request(tmp_path):
    execution_repository = SlurmExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository(["cancelling"])

    await SlurmResultFinalizeStep(execution_repository, repository).post_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.SUCCEEDED


@pytest.mark.asyncio
async def test_result_ready_finalizes_through_execution_repository(tmp_path):
    execution_repository = SlurmExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository(["cancelled"])

    await SlurmResultFinalizeStep(
        execution_repository,
        repository,
        retry_count=0,
    ).post_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert repository.statuses == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.SUCCEEDED


@pytest.mark.asyncio
async def test_finalizer_retries_transient_upload_failure(tmp_path):
    execution_repository = SlurmExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository(
        upload_errors=[TimeoutError("storage unavailable")]
    )

    await SlurmResultFinalizeStep(
        execution_repository,
        repository,
        retry_count=1,
        retry_interval_seconds=0,
    ).post_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert len(repository.uploads) == 2
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.SUCCEEDED
