import hashlib
import json

import pytest

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    TranspileResult,
)
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.simulator import (
    ExecutionState,
    SchedulerJobStatus,
    SlurmSimulatorOptions,
    SchedulerState,
    SlurmSubmissionUncertainError,
    build_execution_request,
    canonical_request_json,
    request_hash,
)
from oqtopus_engine_core.steps import (
    JobCancelledError,
    SimulatorLifecycleStep,
    SlurmSimulatorStep,
)

from ..simulator.execution.in_memory_execution_repository import (
    InMemoryExecutionRepository as ExecutionRepository,
)

SAMPLING_QASM = """
OPENQASM 3;
include "stdgates.inc";
bit[1] c;
qubit[1] q;
x q[0];
c[0] = measure q[0];
"""


def make_job() -> Job:
    return Job(
        job_id="job-1",
        device_id="large-simulator",
        shots=10,
        job_type="sampling",
        input="https://example.invalid/input.zip",
        transpile_result=TranspileResult(
            transpiled_program=SAMPLING_QASM,
            stats={},
            virtual_physical_mapping={"qubit_mapping": {"0": 0}},
        ),
        transpiler_info={},
        simulator_info={"n_nodes": 1, "n_per_node": 1},
        mitigation_info={},
        status="ready",
    )


class RecordingJobRepository(NullJobRepository):
    def __init__(
        self,
        statuses: list[str | Exception],
        update_errors: list[Exception] | None = None,
    ):
        super().__init__()
        self.statuses = statuses
        self.update_errors = update_errors or []
        self.updated_statuses: list[str] = []
        self.current_status = "running"

    async def get_job(self, job_id: str):
        job = make_job()
        job.job_id = job_id
        status = self.statuses.pop(0) if self.statuses else self.current_status
        if isinstance(status, Exception):
            raise status
        self.current_status = status
        job.status = status
        return job

    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        self.updated_statuses.append(job.status)
        if self.update_errors:
            raise self.update_errors.pop(0)


class StubSlurmClient:
    def __init__(
        self,
        statuses: list[SchedulerJobStatus | Exception],
        result: dict | None = None,
        found_job_id: str | None = None,
        cancel_error: Exception | None = None,
        submit_error: Exception | None = None,
    ):
        self.statuses = statuses
        self.result = result
        self.found_job_id = found_job_id
        self.cancel_error = cancel_error
        self.submit_error = submit_error
        self.submit_count = 0
        self.find_calls: list[dict] = []
        self.submit_calls: list[dict] = []
        self.cancelled: list[str] = []

    async def find_job(self, **kwargs):
        self.find_calls.append(kwargs)
        return self.found_job_id

    async def submit(self, **kwargs):
        self.submit_count += 1
        self.submit_calls.append(kwargs)
        if self.submit_error is not None:
            raise self.submit_error
        if self.result is not None:
            (kwargs["work_dir"] / "result.json").write_text(
                json.dumps(self.result),
                encoding="utf-8",
            )
        return "12345"

    async def get_status(self, job_id: str):
        status = self.statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return status

    async def cancel(self, job_id: str):
        self.cancelled.append(job_id)
        if self.cancel_error is not None:
            raise self.cancel_error


def make_step(tmp_path, client, job_reader):
    return SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=ExecutionRepository(tmp_path / "repository-placeholder"),
        job_reader=job_reader,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )


def make_simulator_lifecycle_step(step):
    return SimulatorLifecycleStep(
        execution_repository=step._execution_repository,
        job_reader=step._job_reader,
        work_root=str(step._work_root),
        finalize_retry_count=0,
    )


def test_step_expands_user_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    step = SlurmSimulatorStep(
        slurm_client=StubSlurmClient([]),
        execution_repository=ExecutionRepository(tmp_path / "repository-placeholder"),
        job_reader=RecordingJobRepository([]),
        work_root="~/work",
        batch_script="~/batch.sh",
        worker_script="~/worker.py",
    )

    assert step._work_root == home / "work"
    assert step._batch_script == home / "batch.sh"
    assert step._worker_script == home / "worker.py"


@pytest.mark.asyncio
async def test_step_submits_expanded_user_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    client = StubSlurmClient(
        [SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    repository = RecordingJobRepository(["ready", "running", "running"])
    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=ExecutionRepository(tmp_path / "repository-placeholder"),
        job_reader=repository,
        work_root="~/work",
        batch_script="~/batch.sh",
        worker_script="~/worker.py",
        poll_interval_seconds=0,
    )

    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        make_job(),
    )

    assert (
        client.submit_calls[0]["work_dir"]
        == home / "work" / hashlib.sha256(b"job-1").hexdigest()[:32]
    )
    assert client.submit_calls[0]["batch_script"] == home / "batch.sh"
    assert client.submit_calls[0]["worker_script"] == home / "worker.py"


async def run_root_step(step, gctx, job):
    await make_simulator_lifecycle_step(step).pre_process(gctx, JobContext(), job)
    return await step.pre_process(gctx, JobContext(), job)


@pytest.mark.asyncio
async def test_sampling_submits_and_restores_result(tmp_path):
    client = StubSlurmClient(
        [
            SchedulerJobStatus("12345", SchedulerState.PENDING, "PENDING"),
            SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0"),
        ],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 2.5,
        },
    )
    repository = RecordingJobRepository(["ready", "running", "running"])
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    job = make_job()
    job.simulator_info = {"n_nodes": 2, "n_per_node": 3}

    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        job,
    )

    assert repository.updated_statuses == ["running"]
    assert client.submit_count == 1
    assert client.submit_calls[0]["nodes"] == 2
    assert client.submit_calls[0]["tasks_per_node"] == 3
    assert job.result is not None
    assert job.result.sampling is not None
    assert job.result.sampling.counts == {"1": 10}
    assert job.execution_time == 2.5
    record = await execution_repository.get(job.job_id)
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_execution_start_reconciles_lost_cloud_response(tmp_path):
    client = StubSlurmClient(
        [SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    repository = RecordingJobRepository(
        ["ready", "running", "running"],
        update_errors=[TimeoutError("response lost")],
    )
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")

    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        make_job(),
    )

    assert client.submit_count == 1
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_execution_start_lost_response_then_cancellation_request(tmp_path):
    client = StubSlurmClient([
        SchedulerJobStatus("12345", SchedulerState.RUNNING, "RUNNING"),
        SchedulerJobStatus("12345", SchedulerState.CANCELLED, "CANCELLED"),
    ])
    repository = RecordingJobRepository(
        ["ready", "cancelling", "cancelling"],
        update_errors=[TimeoutError("response lost")],
    )
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")

    with pytest.raises(JobCancelledError, match="before SLURM submission"):
        await SimulatorLifecycleStep(
            execution_repository=execution_repository,
            job_reader=repository,
            work_root=str(tmp_path / "work"),
        ).pre_process(
            GlobalContext(config={}, job_repository=repository),
            JobContext(),
            make_job(),
        )

    assert client.submit_count == 0
    assert client.cancelled == []
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.CANCELLED


@pytest.mark.asyncio
async def test_execution_start_race_with_cloud_cancellation(tmp_path):
    client = StubSlurmClient([])
    repository = RecordingJobRepository(
        ["ready", "cancelled"],
        update_errors=[RuntimeError("status conflict")],
    )
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")

    with pytest.raises(JobCancelledError, match="before SLURM submission"):
        await SimulatorLifecycleStep(
            execution_repository=execution_repository,
            job_reader=repository,
            work_root=str(tmp_path / "work"),
        ).pre_process(
            GlobalContext(config={}, job_repository=repository),
            JobContext(),
            make_job(),
        )

    assert client.submit_count == 0
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.CANCELLED


@pytest.mark.asyncio
async def test_poll_recovers_from_transient_cloud_and_slurm_errors(tmp_path):
    client = StubSlurmClient(
        [
            RuntimeError("controller unavailable"),
            SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0"),
        ],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    repository = RecordingJobRepository([
        "ready",
        TimeoutError("Cloud unavailable"),
        "running",
        "running",
    ])
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")

    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        make_job(),
    )

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_poll_retries_empty_scheduler_observation(tmp_path):
    client = StubSlurmClient(
        [
            None,
            SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0"),
        ],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    repository = RecordingJobRepository(["ready", "running", "running"])

    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    await run_root_step(
        step,
        GlobalContext(
            config={},
            job_repository=repository,
        ),
        make_job(),
    )

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_cloud_cancel_is_propagated_to_slurm(tmp_path):
    client = StubSlurmClient([
        SchedulerJobStatus("12345", SchedulerState.RUNNING, "RUNNING"),
        SchedulerJobStatus("12345", SchedulerState.CANCELLED, "CANCELLED"),
    ])
    repository = RecordingJobRepository(["ready", "running", "cancelling"])
    step = make_step(tmp_path, client, repository)

    with pytest.raises(JobCancelledError, match="confirmed"):
        await run_root_step(
            step,
            GlobalContext(config={}, job_repository=repository),
            make_job(),
        )

    assert client.cancelled == ["12345"]


@pytest.mark.asyncio
async def test_cloud_cancel_preserves_completed_result_artifact(tmp_path):
    client = StubSlurmClient(
        [
            SchedulerJobStatus("12345", SchedulerState.RUNNING, "RUNNING"),
            SchedulerJobStatus("12345", SchedulerState.CANCELLED, "CANCELLED"),
        ],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    repository = RecordingJobRepository([
        "ready",
        "running",
        "cancelling",
        "cancelling",
    ])
    step = make_step(tmp_path, client, repository)
    job = make_job()

    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        job,
    )

    assert client.cancelled == ["12345"]
    assert job.result is not None
    record = await step._execution_repository.get(job.job_id)
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_cloud_cancel_does_not_cancel_completed_allocation(tmp_path):
    client = StubSlurmClient(
        [SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    repository = RecordingJobRepository(["ready", "running", "cancelling"])
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    job = make_job()

    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        job,
    )

    assert client.cancelled == []
    assert job.result is not None
    record = await execution_repository.get(job.job_id)
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_recovered_cancelling_job_accepts_completed_result(tmp_path):
    result = {
        "schema_version": 1,
        "status": "succeeded",
        "job_type": "sampling",
        "counts": {"1": 10},
        "duration_seconds": 1.0,
    }
    client = StubSlurmClient([
        SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")
    ])
    repository = RecordingJobRepository(["cancelling"])
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    work_root = tmp_path / "work"
    work_dir = work_root / hashlib.sha256(b"job-1").hexdigest()[:32]
    work_dir.mkdir(parents=True)
    request_path = work_dir / "request.json"
    result_path = work_dir / "result.json"
    (work_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    job = make_job()
    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(work_root),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    request, options = step._resolve_request(job, None, request_path)
    request_path.write_text(canonical_request_json(request), encoding="utf-8")
    await execution_repository.initialize()
    await execution_repository.claim("job-1", "sampling")
    await execution_repository.prepare(
        cloud_job_id="job-1",
        request_hash=request_hash(request, options),
        options=options.model_dump(),
        work_dir=work_dir,
        request_path=request_path,
        result_path=result_path,
    )
    await execution_repository.update(
        "job-1",
        ExecutionState.RUNNING,
        slurm_job_id="12345",
        cloud_status="cancelling",
    )

    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        job,
    )

    assert client.submit_count == 0
    assert client.cancelled == []
    assert job.result is not None
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY


@pytest.mark.asyncio
async def test_result_ready_missing_artifact_preserves_checkpoint(tmp_path):
    client = StubSlurmClient(
        [SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    repository = RecordingJobRepository(["ready", "running"])
    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    gctx = GlobalContext(
        config={},
        job_repository=repository,
    )

    await run_root_step(step, gctx, make_job())
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY
    assert record.result_path is not None
    result_path = tmp_path / "work" / hashlib.sha256(b"job-1").hexdigest()[:32]
    (result_path / "result.json").unlink()

    with pytest.raises(RuntimeError, match="validated SLURM result is missing"):
        await step.pre_process(gctx, JobContext(), make_job())

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RESULT_READY
    assert client.submit_count == 1


@pytest.mark.asyncio
async def test_cloud_cancel_failure_remains_recoverable(tmp_path):
    client = StubSlurmClient(
        [SchedulerJobStatus("12345", SchedulerState.RUNNING, "RUNNING")],
        cancel_error=RuntimeError("controller unavailable"),
    )
    repository = RecordingJobRepository(["ready", "running", "cancelling"])
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="controller unavailable"):
        await run_root_step(
            step,
            GlobalContext(config={}, job_repository=repository),
            make_job(),
        )

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RUNNING
    assert record.cloud_status == "running"


@pytest.mark.asyncio
async def test_cancelling_job_transitions_to_failed_on_slurm_failure(tmp_path):
    client = StubSlurmClient([
        SchedulerJobStatus("12345", SchedulerState.FAILED, "TIMEOUT", "1:0")
    ])
    repository = RecordingJobRepository(["ready", "running", "cancelling"])
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")

    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )
    with pytest.raises(RuntimeError, match="TIMEOUT"):
        await run_root_step(
            step,
            GlobalContext(config={}, job_repository=repository),
            make_job(),
        )

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RUNNING


@pytest.mark.asyncio
async def test_submitted_job_transitions_through_ready(tmp_path):
    client = StubSlurmClient(
        [SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")],
        result={
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        },
    )
    repository = RecordingJobRepository(["submitted", "running"])

    step = make_step(tmp_path, client, repository)
    await run_root_step(
        step,
        GlobalContext(config={}, job_repository=repository),
        make_job(),
    )

    assert repository.updated_statuses == ["ready", "running"]


@pytest.mark.asyncio
async def test_reconciled_allocation_is_not_submitted_again(tmp_path):
    job = make_job()
    options = SlurmSimulatorOptions.model_validate(job.simulator_info)
    digest = request_hash(build_execution_request(job, options), options)
    job_token = hashlib.sha256(job.job_id.encode()).hexdigest()[:16]
    result = {
        "schema_version": 1,
        "status": "succeeded",
        "job_type": "sampling",
        "counts": {"1": 10},
        "duration_seconds": 1.0,
    }
    token = hashlib.sha256(b"job-1").hexdigest()[:32]
    work_dir = tmp_path / "work" / token
    work_dir.mkdir(parents=True)
    (work_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    client = StubSlurmClient(
        [SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")],
        found_job_id="12345",
    )
    repository = RecordingJobRepository(["running", "running"])

    step = make_step(tmp_path, client, repository)
    await run_root_step(
        step,
        GlobalContext(
            config={},
            job_repository=repository,
        ),
        job,
    )

    assert client.submit_count == 0
    assert len(client.find_calls) == 1
    assert set(client.find_calls[0]) == {"job_name", "start_time"}
    assert client.find_calls[0]["job_name"] == (f"oqtopus-{job_token}-{digest[:16]}")


@pytest.mark.asyncio
async def test_uncertain_submission_preserves_submit_intent(tmp_path):
    client = StubSlurmClient(
        [],
        submit_error=SlurmSubmissionUncertainError("uncertain"),
    )
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    repository = RecordingJobRepository(["ready"])
    step = SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    )

    with pytest.raises(SlurmSubmissionUncertainError):
        await run_root_step(
            step,
            GlobalContext(
                config={},
                job_repository=repository,
            ),
            make_job(),
        )

    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RUNNING


@pytest.mark.asyncio
async def test_recovered_submit_intent_without_match_is_not_resubmitted(tmp_path):
    job = make_job()
    options = SlurmSimulatorOptions(n_nodes=1, n_per_node=1)
    request = build_execution_request(job, options)
    token = hashlib.sha256(job.job_id.encode()).hexdigest()[:32]
    work_dir = tmp_path / "work" / token
    work_dir.mkdir(parents=True)
    request_path = work_dir / "request.json"
    request_path.write_text(canonical_request_json(request), encoding="utf-8")
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim(job.job_id, job.job_type)
    await execution_repository.prepare(
        cloud_job_id=job.job_id,
        request_hash=request_hash(request, options),
        options=options.model_dump(),
        work_dir=work_dir,
        request_path=request_path,
        result_path=work_dir / "result.json",
    )
    await execution_repository.update(
        job.job_id,
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
        cloud_status="running",
    )
    job.transpile_result = None
    client = StubSlurmClient([])
    repository = RecordingJobRepository(["running"])

    with pytest.raises(RuntimeError, match="manual reconciliation"):
        await SlurmSimulatorStep(
            slurm_client=client,
            execution_repository=execution_repository,
            job_reader=repository,
            work_root=str(tmp_path / "work"),
            batch_script="/opt/oqtopus/run.sh",
            worker_script="/opt/oqtopus/run_qulacs_mpi.py",
            poll_interval_seconds=0,
        ).pre_process(
            GlobalContext(
                config={},
                job_repository=repository,
            ),
            JobContext(),
            job,
        )

    assert client.submit_count == 0
    record = await execution_repository.get("job-1")
    assert record is not None
    assert record.state is ExecutionState.RUNNING


@pytest.mark.asyncio
async def test_restart_reattaches_from_persisted_request(tmp_path):
    job = make_job()
    options = SlurmSimulatorOptions(n_nodes=1, n_per_node=1)
    request = build_execution_request(job, options)
    token = hashlib.sha256(job.job_id.encode()).hexdigest()[:32]
    work_dir = tmp_path / "work" / token
    work_dir.mkdir(parents=True)
    request_path = work_dir / "request.json"
    result_path = work_dir / "result.json"
    request_path.write_text(canonical_request_json(request), encoding="utf-8")
    result_path.write_text(
        json.dumps({
            "schema_version": 1,
            "status": "succeeded",
            "job_type": "sampling",
            "counts": {"1": 10},
            "duration_seconds": 1.0,
        }),
        encoding="utf-8",
    )
    execution_repository = ExecutionRepository(tmp_path / "repository-placeholder")
    await execution_repository.initialize()
    await execution_repository.claim(job.job_id, job.job_type)
    await execution_repository.prepare(
        cloud_job_id=job.job_id,
        request_hash=request_hash(request, options),
        options=options.model_dump(),
        work_dir=work_dir,
        request_path=request_path,
        result_path=result_path,
    )
    await execution_repository.update(
        job.job_id,
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
        cloud_status="running",
    )
    await execution_repository.update(
        job.job_id,
        ExecutionState.RUNNING,
        expected={ExecutionState.RUNNING},
        slurm_job_id="12345",
    )
    job.transpile_result = None
    client = StubSlurmClient([
        SchedulerJobStatus("12345", SchedulerState.COMPLETED, "COMPLETED", "0:0")
    ])
    repository = RecordingJobRepository(["running", "running"])

    await SlurmSimulatorStep(
        slurm_client=client,
        execution_repository=execution_repository,
        job_reader=repository,
        work_root=str(tmp_path / "work"),
        batch_script="/opt/oqtopus/run.sh",
        worker_script="/opt/oqtopus/run_qulacs_mpi.py",
        poll_interval_seconds=0,
    ).pre_process(
        GlobalContext(
            config={},
            job_repository=repository,
        ),
        JobContext(),
        job,
    )

    assert client.submit_count == 0
    assert job.result is not None
    assert job.result.sampling is not None
    assert job.result.sampling.counts == {"1": 10}
