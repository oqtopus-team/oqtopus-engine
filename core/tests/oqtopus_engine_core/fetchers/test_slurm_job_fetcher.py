from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.fetchers import SlurmJobFetcher
from oqtopus_engine_core.framework import Device, GlobalContext, Job
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.simulator import (
    ExecutionState,
    SchedulerJobStatus,
    SchedulerState,
)
from ..simulator.execution.in_memory_execution_repository import (
    InMemoryExecutionRepository as ExecutionRepository,
)


def make_job(status: str) -> Job:
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


class StubJobRepository(NullJobRepository):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self.download_count = 0
        self.updated_statuses: list[str] = []

    async def get_job(self, job_id: str):
        return self.job.model_copy(deep=True)

    async def get_jobs(self, device_id: str, status: str = "ready", limit: int = 10):
        if self.job.status == status:
            return [self.job.model_copy(deep=True)]
        return []

    async def download_job_input(self, job: Job):
        self.download_count += 1
        return {"program": ["OPENQASM 3; qubit q;"]}

    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        assert execution_time is None
        self.updated_statuses.append(job.status)


class RecordingPipeline:
    def __init__(self):
        self.jobs: list[Job] = []

    async def execute_pipeline(self, gctx, jctx, job):
        self.jobs.append(job)


class RecordingSlurmClient:
    def __init__(self):
        self.cancelled: list[str] = []
        self.status = SchedulerJobStatus("12345", SchedulerState.RUNNING, "RUNNING")

    async def cancel(self, job_id: str):
        self.cancelled.append(job_id)

    async def get_status(self, job_id: str):
        return self.status


class CloudDerivedReadyExecutionRepository(ExecutionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.claim_count = 0

    async def initialize(self) -> None:
        await super().claim("job-1", "sampling")

    async def claim(self, _cloud_job_id: str, _job_type: str) -> bool:
        self.claim_count += 1
        return True


def make_fetcher(
    database_path: Path,
    repository: StubJobRepository,
    pipeline: RecordingPipeline,
    work_root: Path | None = None,
    artifact_ttl_seconds: int = 604800,
):
    execution_repository = ExecutionRepository(database_path)
    slurm_client = RecordingSlurmClient()
    fetcher = SlurmJobFetcher(
        execution_repository,
        repository,
        slurm_client,  # type: ignore[arg-type]
        work_root=str(work_root) if work_root is not None else None,
        artifact_ttl_seconds=artifact_ttl_seconds,
    )
    fetcher.gctx = GlobalContext(
        config={},
        job_repository=repository,
        device=Device(
            device_id="large-simulator",
            device_type="simulator",
            status="active",
            n_qubits=40,
            basis_gates=[],
            instructions=[],
            description="",
            is_connected=True,
        ),
    )
    fetcher.pipeline = pipeline  # type: ignore[assignment]
    return fetcher, execution_repository, slurm_client


@pytest.mark.asyncio
async def test_recover_unfinished_running_job(tmp_path):
    pipeline = RecordingPipeline()
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("running")),
        pipeline,
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")

    await fetcher.recover_unfinished()

    assert [job.job_id for job in pipeline.jobs] == ["job-1"]
    assert pipeline.jobs[0].program == ["OPENQASM 3; qubit q;"]


@pytest.mark.asyncio
async def test_recover_requeues_derived_cancelling_job(tmp_path):
    pipeline = RecordingPipeline()
    job = make_job("cancelling")
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(job),
        pipeline,
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        slurm_job_id="12345",
        cloud_status="cancelling",
    )

    await fetcher.recover_unfinished()

    assert len(pipeline.jobs) == 1
    assert pipeline.jobs[0].status == "cancelling"


@pytest.mark.asyncio
async def test_startup_recovery_retries_transient_failure(tmp_path):
    fetcher, _, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("running")),
        RecordingPipeline(),
    )
    fetcher._interval_seconds = 0
    fetcher.recover_unfinished = AsyncMock(
        side_effect=[TimeoutError("Cloud unavailable"), None]
    )

    await fetcher._recover_until_ready()

    assert fetcher.recover_unfinished.await_count == 2


@pytest.mark.asyncio
async def test_poll_claims_submitted_job_only_once(tmp_path):
    pipeline = RecordingPipeline()
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("submitted")),
        pipeline,
    )
    await execution_repository.initialize()

    assert await fetcher.poll_once() == 1
    assert await fetcher.poll_once() == 0

    assert [job.job_id for job in pipeline.jobs] == ["job-1"]
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.READY


@pytest.mark.asyncio
async def test_poll_claims_cloud_derived_ready_execution():
    repository = StubJobRepository(make_job("ready"))
    pipeline = RecordingPipeline()
    execution_repository = CloudDerivedReadyExecutionRepository()
    fetcher = SlurmJobFetcher(
        execution_repository,
        repository,
        RecordingSlurmClient(),  # type: ignore[arg-type]
    )
    fetcher.gctx = GlobalContext(
        config={},
        job_repository=repository,
        device=Device(
            device_id="large-simulator",
            device_type="simulator",
            status="active",
            n_qubits=40,
            basis_gates=[],
            instructions=[],
            description="",
            is_connected=True,
        ),
    )
    fetcher.pipeline = pipeline  # type: ignore[assignment]
    await execution_repository.initialize()

    assert await fetcher.poll_once() == 1

    assert execution_repository.claim_count == 1
    assert [job.job_id for job in pipeline.jobs] == ["job-1"]


@pytest.mark.asyncio
async def test_poll_rejects_unsupported_job_before_download(tmp_path):
    job = make_job("submitted")
    job.job_type = "multi_manual"
    repository = StubJobRepository(job)
    pipeline = RecordingPipeline()
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        repository,
        pipeline,
    )
    await execution_repository.initialize()

    assert await fetcher.poll_once() == 1

    assert repository.download_count == 0
    assert repository.updated_statuses == ["failed"]
    assert pipeline.jobs == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.FAILED
    assert record.last_error == "unsupported SLURM simulator job type: multi_manual"


@pytest.mark.asyncio
async def test_recover_cancelled_job_waits_for_scheduler_confirmation(tmp_path):
    pipeline = RecordingPipeline()
    fetcher, execution_repository, slurm_client = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("cancelled")),
        pipeline,
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        slurm_job_id="12345",
    )

    with pytest.raises(RuntimeError, match="pending scheduler confirmation"):
        await fetcher.recover_unfinished()

    assert slurm_client.cancelled == ["12345"]
    assert pipeline.jobs == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RUNNING


@pytest.mark.asyncio
async def test_recover_cancelled_job_after_scheduler_confirmation(tmp_path):
    fetcher, execution_repository, slurm_client = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("cancelled")),
        RecordingPipeline(),
    )
    slurm_client.status = SchedulerJobStatus(
        "12345",
        SchedulerState.CANCELLED,
        "CANCELLED",
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        slurm_job_id="12345",
    )

    await fetcher.recover_unfinished()

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.CANCELLED


@pytest.mark.asyncio
async def test_recover_result_ready_ignores_cloud_cancellation(tmp_path):
    pipeline = RecordingPipeline()
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("cancelled")),
        pipeline,
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update("job-1", ExecutionState.RESULT_READY)

    await fetcher.recover_unfinished()

    assert [job.job_id for job in pipeline.jobs] == ["job-1"]
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_recover_retries_unsynchronized_cloud_failure(tmp_path):
    pipeline = RecordingPipeline()
    repository = StubJobRepository(make_job("running"))
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        repository,
        pipeline,
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.FAILED,
        last_error="allocation disappeared",
    )

    await fetcher.recover_unfinished()

    assert repository.updated_statuses == ["failed"]
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.FAILED
    assert record.cloud_status == "failed"


@pytest.mark.asyncio
async def test_recover_does_not_reverse_unsynchronized_terminal_state(tmp_path):
    repository = StubJobRepository(make_job("cancelled"))
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        repository,
        RecordingPipeline(),
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.update(
        "job-1",
        ExecutionState.FAILED,
        last_error="allocation failed",
    )

    await fetcher.recover_unfinished()

    assert repository.updated_statuses == ["failed"]
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.FAILED
    assert record.cloud_status == "failed"


@pytest.mark.asyncio
async def test_recover_cleans_succeeded_artifacts_immediately(tmp_path):
    work_root = tmp_path / "work"
    work_dir = work_root / "job-token"
    work_dir.mkdir(parents=True)
    request_path = work_dir / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("succeeded")),
        RecordingPipeline(),
        work_root,
    )
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
    await execution_repository.update("job-1", ExecutionState.RESULT_READY)

    await fetcher.recover_unfinished()

    assert not work_dir.exists()
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.SUCCEEDED
    assert record.work_dir is None


@pytest.mark.asyncio
async def test_recover_uses_persisted_request_without_retranspiling(tmp_path):
    pipeline = RecordingPipeline()
    repository = StubJobRepository(make_job("running"))
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        repository,
        pipeline,
    )
    request_path = tmp_path / "work" / "request.json"
    request_path.parent.mkdir()
    request_path.write_text("{}", encoding="utf-8")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.prepare(
        cloud_job_id="job-1",
        request_hash="digest",
        options={},
        work_dir=request_path.parent,
        request_path=request_path,
        result_path=request_path.parent / "result.json",
    )

    await fetcher.recover_unfinished()

    assert repository.download_count == 0
    assert len(pipeline.jobs) == 1
    assert pipeline.jobs[0].program is None
    assert pipeline.jobs[0].transpiler_info == {"transpiler_lib": None}


@pytest.mark.asyncio
async def test_recover_syncs_terminal_before_cleaning_expired_artifacts(tmp_path):
    pipeline = RecordingPipeline()
    work_root = tmp_path / "work"
    work_dir = work_root / "job-token"
    work_dir.mkdir(parents=True)
    request_path = work_dir / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    fetcher, execution_repository, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("failed")),
        pipeline,
        work_root,
        artifact_ttl_seconds=0,
    )
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
    await execution_repository.update("job-1", ExecutionState.FAILED)

    await fetcher.recover_unfinished()

    assert work_dir.exists()
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.cloud_status == "failed"

    await fetcher.recover_unfinished()

    assert not work_dir.exists()
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.work_dir is None
