import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJob,
    JobsJobStatusUpdateResponse,
)
from oqtopus_engine_core.fetchers import SlurmJobFetcher
from oqtopus_engine_core.framework import Device, GlobalContext, Job
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.simulator import (
    ExecutionState,
    LocalExecutionRepository,
    OqtopusCloudExecutionRepository,
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
        self.reconciled_job_names: list[str] = []
        self.status = SchedulerJobStatus("12345", SchedulerState.RUNNING, "RUNNING")

    async def cancel(self, job_id: str):
        self.cancelled.append(job_id)

    async def get_status(self, job_id: str):
        return self.status

    async def find_job(self, *, job_name: str, start_time: datetime | None = None):
        _ = start_time
        self.reconciled_job_names.append(job_name)
        return "12345"


class CloudCancellationRaceApi:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.job = JobsJob(
            job_id="job-1",
            device_id="large-simulator",
            job_type="sampling",
            shots=1,
            input="input.zip",
            status="running",
            transpiler_info={},
            simulator_info={
                "backend": "mpi-qulacs",
                "n_nodes": 1,
                "n_per_node": 1,
            },
            mitigation_info={},
            submitted_at=now,
            ready_at=now,
            running_at=now,
        )

    def get_job(self, *, job_id: str, **_kwargs: object) -> JobsJob:
        assert job_id == self.job.job_id
        self.job.status = "cancelled"
        return self.job

    def get_jobs(
        self,
        *,
        device_id: str,
        status: str | None = None,
        **_kwargs: object,
    ) -> list[JobsJob]:
        assert device_id == self.job.device_id
        if status == "running":
            return [deepcopy(self.job)]
        return []


def write_recovery_request(tmp_path: Path) -> Path:
    token = hashlib.sha256(b"job-1").hexdigest()[:32]
    work_dir = tmp_path / "work" / token
    work_dir.mkdir(parents=True)
    request_path = work_dir / "request.json"
    request_path.write_text(
        '{"schema_version":1,"job_type":"sampling","n_qubits":1,'
        '"gates":[],"measurement_mapping":{"0":0},"shots":1,'
        '"operators":[],"seed_simulation":7,"n_per_node":1}',
        encoding="utf-8",
    )
    return request_path


class CancelledJobsApi(CloudCancellationRaceApi):
    """Cloud already reports cancelled before Core starts recovery."""

    def __init__(self):
        super().__init__()
        self.job.status = "cancelled"
        self.job.ended_at = datetime.now(UTC) - timedelta(days=8)

    def get_jobs(self, *, device_id, status=None, **kwargs):
        return [deepcopy(self.job)] if status == self.job.status else []


def make_cancelled_cloud_fetcher(tmp_path, *, cleanup=False):
    api = CancelledJobsApi()
    repository = OqtopusCloudExecutionRepository(
        device_id="large-simulator", work_root=str(tmp_path / "work"), jobs_api=api
    )
    pipeline = RecordingPipeline()
    fetcher, _, client = make_fetcher(
        tmp_path,
        StubJobRepository(make_job("cancelled")),
        pipeline,
        work_root=tmp_path / "work" if cleanup else None,
    )
    fetcher._execution_repository = repository
    fetcher._job_reader = repository
    return fetcher, client, pipeline


@pytest.mark.asyncio
async def test_startup_recovers_already_cancelled_cloud_allocation(tmp_path):
    request = write_recovery_request(tmp_path)
    fetcher, client, pipeline = make_cancelled_cloud_fetcher(tmp_path)

    with pytest.raises(RuntimeError, match="pending scheduler confirmation"):
        await fetcher.recover_unfinished()

    assert client.cancelled == ["12345"]
    assert request.exists()
    assert pipeline.jobs == []
    client.status = SchedulerJobStatus("12345", SchedulerState.CANCELLED, "CANCELLED")
    await fetcher.recover_unfinished()
    assert client.cancelled == ["12345"]
    assert pipeline.jobs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [SchedulerState.RUNNING, SchedulerState.UNKNOWN, None]
)
async def test_ttl_does_not_delete_unconfirmed_cancelled_allocation(tmp_path, state):
    request = write_recovery_request(tmp_path)
    fetcher, client, _ = make_cancelled_cloud_fetcher(tmp_path, cleanup=True)
    client.status = SchedulerJobStatus("12345", state, state.name) if state else None

    with pytest.raises(RuntimeError, match="pending"):
        await fetcher.recover_unfinished()

    assert request.exists()
    client.status = SchedulerJobStatus("12345", SchedulerState.CANCELLED, "CANCELLED")
    await fetcher.recover_unfinished()
    assert not request.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", [False, True])
async def test_result_published_during_cancellation_recovery_is_enqueued(
    tmp_path, cleanup
):
    request = write_recovery_request(tmp_path)
    fetcher, client, pipeline = make_cancelled_cloud_fetcher(tmp_path, cleanup=cleanup)

    async def publish_result(job_id):
        (request.parent / "result.json").write_text(
            '{"schema_version":1,"job_type":"sampling","status":"succeeded",'
            '"counts":{"0":1},"duration_seconds":1.0}'
        )
        return SchedulerJobStatus(job_id, SchedulerState.COMPLETED, "COMPLETED", "0:0")

    client.get_status = publish_result
    await fetcher.recover_unfinished()

    assert [job.job_id for job in pipeline.jobs] == ["job-1"]
    assert (
        await fetcher._execution_repository.get("job-1")
    ).state is ExecutionState.RESULT_READY
    assert request.exists()
    assert client.cancelled == []


@pytest.mark.asyncio
async def test_cancelled_completed_allocation_without_result_is_preserved(tmp_path):
    request = write_recovery_request(tmp_path)
    fetcher, client, pipeline = make_cancelled_cloud_fetcher(tmp_path, cleanup=True)
    client.status = SchedulerJobStatus(
        "12345", SchedulerState.COMPLETED, "COMPLETED", "0:0"
    )

    with pytest.raises(RuntimeError, match="has no result"):
        await fetcher.recover_unfinished()

    assert request.exists()
    assert pipeline.jobs == []
    assert client.cancelled == []


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
    work_root: str | Path | None = None,
    artifact_ttl_seconds: int = 604800,
    batch_script: str | None = None,
    worker_script: str | None = None,
):
    execution_repository = ExecutionRepository(database_path)
    slurm_client = RecordingSlurmClient()
    fetcher = SlurmJobFetcher(
        execution_repository,
        repository,
        slurm_client,  # type: ignore[arg-type]
        work_root=str(work_root) if work_root is not None else None,
        artifact_ttl_seconds=artifact_ttl_seconds,
        batch_script=batch_script,
        worker_script=worker_script,
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


def test_runtime_path_preflight_rejects_relative_work_root(tmp_path):
    fetcher, _, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("running")),
        RecordingPipeline(),
    )
    fetcher._work_root = Path("relative/work")

    with pytest.raises(ValueError, match="work root must be absolute"):
        fetcher.validate_runtime_paths()


def test_runtime_path_preflight_expands_user_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    batch_script = home / "batch.sh"
    batch_script.write_text("#!/bin/sh\n", encoding="utf-8")
    batch_script.chmod(0o755)
    (home / "worker.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    fetcher, _, _ = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("running")),
        RecordingPipeline(),
        work_root="~/work",
        batch_script="~/batch.sh",
        worker_script="~/worker.py",
    )

    fetcher.validate_runtime_paths()

    assert fetcher._work_root == home / "work"
    assert fetcher._batch_script == home / "batch.sh"
    assert fetcher._worker_script == home / "worker.py"


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
@pytest.mark.parametrize("operation", ["get", "claim"])
@pytest.mark.parametrize("failed_job_id", ["job-1", "job-2", "job-3"])
async def test_poll_isolates_claim_errors_and_retries_failed_candidate(
    tmp_path, caplog, operation, failed_job_id
):
    jobs = {
        job_id: JobsJob(
            job_id=job_id,
            device_id="large-simulator",
            job_type="sampling",
            shots=10,
            input="input.zip",
            status="ready",
            transpiler_info={},
            simulator_info={},
            mitigation_info={},
        )
        for job_id in ("job-1", "job-2", "job-3")
    }
    fault_enabled = True

    def get_job(*, job_id, **kwargs):
        if fault_enabled and job_id == failed_job_id and operation == "get":
            raise TimeoutError("Cloud read failed")
        return deepcopy(jobs[job_id])

    def patch_job(*, job_id, body, **kwargs):
        if fault_enabled and job_id == failed_job_id and operation == "claim":
            raise TimeoutError("Cloud claim failed before update")
        jobs[job_id].status = body.status
        return JobsJobStatusUpdateResponse(message="Job status updated")

    async def get_jobs(device_id, status, limit):
        return [Job(**job.to_dict()) for job in jobs.values() if job.status == status]

    cloud = StubJobRepository(make_job("ready"))
    cloud.get_jobs = get_jobs
    api = Mock()
    api.get_job.side_effect = get_job
    api.patch_job.side_effect = patch_job
    pipeline = RecordingPipeline()
    fetcher, _, _ = make_fetcher(tmp_path, cloud, pipeline)
    fetcher._execution_repository = OqtopusCloudExecutionRepository(
        device_id="large-simulator", work_root=str(tmp_path / "work"), jobs_api=api
    )
    expected_ids = [job_id for job_id in jobs if job_id != failed_job_id]

    assert await fetcher.poll_once() == 2

    assert [job.job_id for job in pipeline.jobs] == expected_ids
    assert all(jobs[job_id].status == "running" for job_id in expected_ids)
    assert jobs[failed_job_id].status == "ready"
    assert fetcher._scheduled_job_ids == set(expected_ids)
    assert any(
        record.job_id == failed_job_id and record.exc_info is not None
        for record in caplog.records
        if hasattr(record, "job_id")
    )

    fault_enabled = False
    assert await fetcher.poll_once() == 1
    assert await fetcher.poll_once() == 0
    assert [job.job_id for job in pipeline.jobs] == [*expected_ids, failed_job_id]
    assert all(job.status == "running" for job in jobs.values())


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
async def test_recover_cloud_cancelled_job_reconciles_missing_slurm_id(tmp_path):
    api = CloudCancellationRaceApi()
    execution_repository = OqtopusCloudExecutionRepository(
        device_id="large-simulator",
        work_root=str(tmp_path / "work"),
        jobs_api=api,
    )
    write_recovery_request(tmp_path)
    slurm_client = RecordingSlurmClient()
    fetcher = SlurmJobFetcher(
        execution_repository,
        execution_repository,
        slurm_client,  # type: ignore[arg-type]
    )
    fetcher.gctx = GlobalContext(
        config={},
        job_repository=NullJobRepository(),
        device=Device(
            device_id="large-simulator",
            device_type="simulator",
            status="active",
            n_qubits=1,
            basis_gates=[],
            instructions=[],
            description="",
            is_connected=True,
        ),
    )
    fetcher.pipeline = RecordingPipeline()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="pending scheduler confirmation"):
        await fetcher.recover_unfinished()

    assert len(slurm_client.reconciled_job_names) == 1
    assert slurm_client.reconciled_job_names[0].startswith("oqtopus-")
    assert slurm_client.cancelled == ["12345"]


@pytest.mark.asyncio
async def test_recover_unsynchronized_cancelled_record_checks_scheduler(tmp_path):
    work_root = tmp_path / "work"
    fetcher, execution_repository, slurm_client = make_fetcher(
        tmp_path / "repository-placeholder",
        StubJobRepository(make_job("cancelled")),
        RecordingPipeline(),
        work_root,
    )
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    request_path = write_recovery_request(tmp_path)
    await execution_repository.prepare(
        cloud_job_id="job-1",
        request_hash="digest",
        options={"backend": "mpi-qulacs", "n_nodes": 1, "n_per_node": 1},
        work_dir=work_root / request_path.parent.name,
        request_path=request_path,
        result_path=request_path.parent / "result.json",
    )
    await execution_repository.update("job-1", ExecutionState.CANCELLED)

    with pytest.raises(RuntimeError, match="pending scheduler confirmation"):
        await fetcher.recover_unfinished()

    assert len(slurm_client.reconciled_job_names) == 1
    assert slurm_client.cancelled == ["12345"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_cancelled_listing_preserves_newer_terminal_cloud_status(tmp_path, status):
    cloud = StubJobRepository(make_job(status))
    fetcher, records, _ = make_fetcher(tmp_path, cloud, RecordingPipeline())
    await records.claim("job-1", "sampling")
    await records.update("job-1", ExecutionState.CANCELLED)

    await fetcher.recover_unfinished()

    assert cloud.updated_statuses == []
    assert (await records.get("job-1")).cloud_status == status


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
async def test_restart_retries_child_cleanup_only_after_root_finishes(tmp_path):
    work_root = tmp_path / "work"
    local = LocalExecutionRepository(work_root)
    await local.claim("child", "sampling", parent_job_id="job-1")
    await local.update("child", ExecutionState.SUCCEEDED)
    cloud = StubJobRepository(make_job("running"))
    fetcher, _, _ = make_fetcher(
        tmp_path, cloud, RecordingPipeline(), work_root, artifact_ttl_seconds=0
    )

    await fetcher._cleanup_expired_child_artifacts()
    assert await local.get("child") is not None
    cloud.job.status = "failed"
    await fetcher._cleanup_expired_child_artifacts()
    assert await local.get("child") is None


@pytest.mark.asyncio
async def test_child_cleanup_obeys_ttl_and_preserves_unresolved_records(tmp_path):
    work_root = tmp_path / "work"
    local = LocalExecutionRepository(work_root)
    await local.claim("done", "sampling", parent_job_id="job-1")
    await local.update("done", ExecutionState.FAILED)
    await local.claim("unknown", "sampling", parent_job_id="job-1")
    await local.update("unknown", ExecutionState.RUNNING)
    cloud = StubJobRepository(make_job("failed"))
    fetcher, _, _ = make_fetcher(tmp_path, cloud, RecordingPipeline(), work_root)

    await fetcher._cleanup_expired_child_artifacts()
    assert await local.get("done") is not None
    fetcher._artifact_ttl_seconds = 0
    await fetcher._cleanup_expired_child_artifacts()
    assert await local.get("done") is None
    assert (await local.get("unknown")).state is ExecutionState.RUNNING


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
