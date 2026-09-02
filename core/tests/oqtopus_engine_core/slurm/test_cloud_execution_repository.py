import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJob,
    JobsJobStatusUpdate,
    JobsJobStatusUpdateResponse,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.slurm import (
    ExecutionState,
    OqtopusCloudSlurmExecutionRepository,
)


class StubJobsApi:
    def __init__(self) -> None:
        self.job = _job()
        self.patch_calls: list[JobsJobStatusUpdate] = []
        self.patch_response_lost = False
        self.listed_statuses: list[str | None] = []

    def get_job(self, *, job_id: str, **_kwargs: object) -> JobsJob:
        if self.job.job_id != job_id:
            raise ApiException(status=404)
        return self.job

    def patch_job(
        self,
        *,
        body: JobsJobStatusUpdate,
        job_id: str,
        **_kwargs: object,
    ) -> JobsJobStatusUpdateResponse:
        assert self.job.job_id == job_id
        self.patch_calls.append(body)
        if body.status is not None:
            self.job.status = body.status
            if body.status == "running":
                self.job.running_at = datetime.now(UTC)
            elif body.status in {"succeeded", "failed", "cancelled"}:
                self.job.ended_at = datetime.now(UTC)
        if self.patch_response_lost:
            self.patch_response_lost = False
            raise TimeoutError("response lost")
        return JobsJobStatusUpdateResponse(message="Job status updated")

    def get_jobs(
        self,
        *,
        device_id: str,
        status: str | None = None,
        **_kwargs: object,
    ) -> list[JobsJob]:
        assert device_id == self.job.device_id
        self.listed_statuses.append(status)
        return [self.job] if status == self.job.status else []


def _job() -> JobsJob:
    now = datetime.now(UTC)
    return JobsJob(
        job_id="job-1",
        device_id="large-simulator",
        job_type="sampling",
        shots=1,
        input="input.zip",
        status="ready",
        transpiler_info={},
        simulator_info={"backend": "qulacs_mpi", "n_nodes": 2},
        mitigation_info={},
        submitted_at=now,
        ready_at=now,
    )


def _repository(
    tmp_path: Path,
    api: StubJobsApi,
) -> OqtopusCloudSlurmExecutionRepository:
    return OqtopusCloudSlurmExecutionRepository(
        device_id="large-simulator",
        work_root=str(tmp_path / "work"),
        jobs_api=api,
    )


def _local_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    token = hashlib.sha256(b"job-1").hexdigest()[:32]
    work_dir = tmp_path / "work" / token
    return work_dir, work_dir / "request.json", work_dir / "result.json"


@pytest.mark.asyncio
async def test_get_job_preserves_cancelling_status(tmp_path: Path):
    api = StubJobsApi()
    api.job.status = "cancelling"

    job = await _repository(tmp_path, api).get_job("job-1")

    assert job is not None
    assert job.status == "cancelling"


@pytest.mark.asyncio
async def test_claim_uses_existing_running_status(tmp_path: Path):
    api = StubJobsApi()

    assert await _repository(tmp_path, api).claim("job-1", "sampling") is True
    assert api.job.status == "running"
    assert api.patch_calls[-1].status == "running"


@pytest.mark.asyncio
async def test_claim_reconciles_lost_status_response(tmp_path: Path):
    api = StubJobsApi()
    api.patch_response_lost = True

    assert await _repository(tmp_path, api).claim("job-1", "sampling") is True
    assert api.job.status == "running"


@pytest.mark.asyncio
async def test_prepare_keeps_metadata_out_of_cloud(tmp_path: Path):
    api = StubJobsApi()
    repository = _repository(tmp_path, api)
    work_dir, request_path, result_path = _local_paths(tmp_path)

    record = await repository.prepare(
        cloud_job_id="job-1",
        request_hash="a" * 64,
        options={"n_nodes": 2},
        work_dir=work_dir,
        request_path=request_path,
        result_path=result_path,
    )

    assert api.patch_calls == []
    assert record.options == api.job.simulator_info


@pytest.mark.asyncio
async def test_running_job_with_result_artifact_is_result_ready(tmp_path: Path):
    api = StubJobsApi()
    api.job.status = "running"
    work_dir, _, result_path = _local_paths(tmp_path)
    work_dir.mkdir(parents=True)
    result_path.write_text("{}", encoding="utf-8")

    record = await _repository(tmp_path, api).get("job-1")

    assert record is not None
    assert record.state is ExecutionState.RESULT_READY
    assert record.artifact_retained is True
    assert record.slurm_job_id is None


@pytest.mark.asyncio
async def test_cancelling_job_is_recoverable_running_execution(tmp_path: Path):
    api = StubJobsApi()
    api.job.status = "cancelling"
    repository = _repository(tmp_path, api)

    record = await repository.get("job-1")
    unfinished = await repository.list_unfinished()

    assert record is not None
    assert record.state is ExecutionState.RUNNING
    assert record.cloud_status == "cancelling"
    assert [item.cloud_job_id for item in unfinished] == ["job-1"]
    assert api.listed_statuses == ["running", "cancelling"]


@pytest.mark.asyncio
async def test_terminal_update_uses_one_existing_status_patch(tmp_path: Path):
    api = StubJobsApi()
    api.job.status = "running"
    repository = _repository(tmp_path, api)

    record = await repository.update(
        "job-1",
        ExecutionState.SUCCEEDED,
        expected={ExecutionState.RUNNING},
        cloud_status="succeeded",
        output_files=["job-1/result.zip"],
        execution_time=12.5,
    )

    body = api.patch_calls[-1]
    assert body.status == "succeeded"
    assert body.output_files == ["job-1/result.zip"]
    assert body.execution_time == 12.5
    assert record.state is ExecutionState.SUCCEEDED


@pytest.mark.asyncio
async def test_terminal_update_reconciles_lost_response(tmp_path: Path):
    api = StubJobsApi()
    api.job.status = "running"
    api.patch_response_lost = True

    record = await _repository(tmp_path, api).update(
        "job-1",
        ExecutionState.FAILED,
        expected={ExecutionState.RUNNING},
        cloud_status="failed",
        message="failed",
    )

    assert record.state is ExecutionState.FAILED
    assert record.cloud_status == "failed"


@pytest.mark.asyncio
async def test_cleanup_uses_terminal_status_and_local_directory(tmp_path: Path):
    api = StubJobsApi()
    api.job.status = "failed"
    api.job.ended_at = datetime.now(UTC) - timedelta(days=2)
    repository = _repository(tmp_path, api)
    work_dir, _, result_path = _local_paths(tmp_path)
    work_dir.mkdir(parents=True)
    result_path.write_text("{}", encoding="utf-8")

    candidates = await repository.list_cleanup_candidates(
        datetime.now(UTC) - timedelta(days=1)
    )
    removed = await repository.cleanup_artifacts("job-1", tmp_path / "work")

    assert [record.cloud_job_id for record in candidates] == ["job-1"]
    assert api.listed_statuses == ["succeeded", "failed", "cancelled"]
    assert removed is True
    assert not work_dir.exists()


@pytest.mark.asyncio
async def test_get_rejects_execution_from_another_device(tmp_path: Path):
    api = StubJobsApi()
    api.job.device_id = "another-device"

    with pytest.raises(ValueError, match="another device"):
        await _repository(tmp_path, api).get("job-1")
