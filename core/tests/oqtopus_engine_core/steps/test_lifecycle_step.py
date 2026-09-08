import pytest

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobResult,
    SamplingResult,
)
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.slurm import (
    ExecutionState,
    LocalExecutionRepository,
)
from oqtopus_engine_core.steps import SimulatorLifecycleStep

from ..slurm.in_memory_execution_repository import (
    InMemoryExecutionRepository as ExecutionRepository,
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
        self.uploads: list[object] = []
        self.statuses: list[str] = []

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


def make_lifecycle_step(execution_repository, job_reader, work_root, **kwargs):
    return SimulatorLifecycleStep(
        execution_repository=execution_repository,
        job_reader=job_reader,
        work_root=str(work_root),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_sampling_parent_starts_cloud_lifecycle_before_estimator_split(
    tmp_path,
):
    execution_repository = ExecutionRepository(
        tmp_path / "repository-placeholder"
    )
    repository = RecordingJobRepository(["submitted"])
    job = make_job()
    job.job_type = "estimation"
    job.simulator_info = {"estimation_method": "sampling"}

    await make_lifecycle_step(
        execution_repository,
        repository,
        tmp_path / "work",
    ).pre_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        job,
    )

    assert repository.statuses == ["ready", "running"]
    record = await execution_repository.get(job.job_id)
    assert record is not None
    assert record.state is ExecutionState.RUNNING
    assert record.cloud_status == "running"


@pytest.mark.asyncio
async def test_direct_estimation_root_uses_the_same_cloud_lifecycle(tmp_path):
    execution_repository = ExecutionRepository(
        tmp_path / "repository-placeholder"
    )
    repository = RecordingJobRepository(["submitted"])
    job = make_job()
    job.job_type = "estimation"
    job.simulator_info = {"estimation_method": "direct"}

    await make_lifecycle_step(
        execution_repository,
        repository,
        tmp_path / "work",
    ).pre_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        job,
    )

    assert repository.statuses == ["ready", "running"]
    record = await execution_repository.get(job.job_id)
    assert record is not None
    assert record.state is ExecutionState.RUNNING
    assert record.cloud_status == "running"


@pytest.mark.asyncio
async def test_post_process_closes_metadata_with_cloud_update(tmp_path):
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
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

    await make_lifecycle_step(execution_repository, repository, work_root).post_process(
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
async def test_post_process_finalizes_sampling_parent_from_running_state(tmp_path):
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "estimation")
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository()

    await make_lifecycle_step(
        execution_repository,
        repository,
        tmp_path / "work",
    ).post_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.SUCCEEDED


@pytest.mark.asyncio
async def test_post_process_cleans_sampling_child_artifacts_after_join(tmp_path):
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "estimation")
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
    )
    child_repository = LocalExecutionRepository(tmp_path / "work")
    await child_repository.initialize()
    await child_repository.claim("job-1-estimation-0", "sampling")
    work_dir = tmp_path / "work" / "child-artifacts"
    work_dir.mkdir()
    await child_repository.prepare(
        cloud_job_id="job-1-estimation-0",
        request_hash="child-digest",
        options={},
        work_dir=work_dir,
        request_path=work_dir / "request.json",
        result_path=work_dir / "result.json",
    )
    await child_repository.update(
        "job-1-estimation-0",
        ExecutionState.SUCCEEDED,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository()
    job = make_job()
    child = make_job()
    child.job_id = "job-1-estimation-0"
    job.children = [child]

    await make_lifecycle_step(
        execution_repository,
        repository,
        tmp_path / "work",
    ).post_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        job,
    )

    assert not work_dir.exists()


@pytest.mark.asyncio
async def test_result_ready_wins_over_later_cancellation_request(tmp_path):
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository(["cancelling"])

    await make_lifecycle_step(
        execution_repository,
        repository,
        tmp_path / "work",
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
async def test_result_ready_finalizes_through_execution_repository(tmp_path):
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.READY},
    )
    repository = RecordingJobRepository(["cancelled"])

    await make_lifecycle_step(
        execution_repository,
        repository,
        tmp_path / "work",
        finalize_retry_count=0,
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
async def test_post_process_retries_transient_upload_failure(tmp_path):
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
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

    await make_lifecycle_step(
        execution_repository,
        repository,
        tmp_path / "work",
        finalize_retry_count=1,
        finalize_retry_interval_seconds=0,
    ).post_process(
        GlobalContext(config={}, job_repository=repository),
        JobContext(),
        make_job(),
    )

    assert len(repository.uploads) == 2
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.SUCCEEDED