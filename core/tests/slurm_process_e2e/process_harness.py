import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobOutput,
    TranspileResult,
)
from oqtopus_engine_core.handlers import SlurmPipelineExceptionHandler
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    JobsJob,
    JobsJobStatusUpdate,
    JobsJobStatusUpdateResponse,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.repositories import NullJobRepository
from oqtopus_engine_core.slurm import (
    OqtopusCloudExecutionRepository,
    SingleProcessLock,
    SlurmClient,
)
from oqtopus_engine_core.steps import (
    JobCancelledError,
    SimulatorLifecycleStep,
    SlurmSimulatorStep,
)

from .process_support import read_json, replace_json, update_json

# ruff: noqa: D101, D102, D103

SAMPLING_QASM = """
OPENQASM 3;
include "stdgates.inc";
bit[1] c;
qubit[1] q;
x q[0];
c[0] = measure q[0];
"""


def _job(status: str) -> Job:
    return Job(
        job_id="process-e2e-job",
        device_id="large-simulator",
        shots=10,
        job_type="sampling",
        input="fake://input.zip",
        transpile_result=TranspileResult(
            transpiled_program=SAMPLING_QASM,
            stats={},
            virtual_physical_mapping={"qubit_mapping": {"0": 0}},
        ),
        transpiler_info={},
        simulator_info={"backend": "mpi-qulacs", "n_nodes": 1, "n_per_node": 1},
        mitigation_info={},
        status=status,
    )


class FileJobRepository(NullJobRepository):
    def __init__(self, state_path: Path) -> None:
        super().__init__()
        self._state_path = state_path

    async def update_job_status(
        self,
        job: Job,
        execution_time: float | None = None,
    ) -> None:
        def update(state: dict[str, Any]) -> None:
            state["status"] = job.status
            state["message"] = job.message
            state["execution_time"] = execution_time
            if job.status in {"succeeded", "failed", "cancelled"}:
                state.setdefault("ended_at", datetime.now(UTC).isoformat())

        update_json(self._state_path, dict, update)

    async def upload_job_outputs(
        self,
        job: Job,
        outputs: list[JobOutput],
    ) -> None:
        def update(state: dict[str, Any]) -> None:
            state["output_job_id"] = job.job_id
            state["outputs"] = [output[1] for output in outputs]

        update_json(self._state_path, dict, update)


class FileJobsApi:
    """Process-safe Cloud Job API test double."""

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path

    def get_job(
        self,
        *,
        job_id: str,
        **_kwargs: object,
    ) -> JobsJob:
        if job_id != "process-e2e-job":
            raise ApiException(status=404)
        return self._to_job(read_json(self._state_path))

    def patch_job(
        self,
        *,
        body: JobsJobStatusUpdate,
        job_id: str,
        **_kwargs: object,
    ) -> JobsJobStatusUpdateResponse:
        if job_id != "process-e2e-job":
            raise ApiException(status=404)

        def patch(state: dict[str, Any]) -> bool:
            if body.status is None:
                raise ApiException(status=400)
            state["status"] = body.status
            if body.status in {"succeeded", "failed", "cancelled"}:
                state.setdefault("ended_at", datetime.now(UTC).isoformat())
            return True

        update_json(self._state_path, dict, patch)
        return JobsJobStatusUpdateResponse(message="Job status updated")

    def get_jobs(
        self,
        *,
        device_id: str,
        status: str | None = None,
        **_kwargs: object,
    ) -> list[JobsJob]:
        state = read_json(self._state_path)
        if device_id != "large-simulator":
            return []
        return [self._to_job(state)] if status == state["status"] else []

    @classmethod
    def _to_job(cls, state: dict[str, Any]) -> JobsJob:
        ended_at = state.get("ended_at")
        return JobsJob(
            job_id="process-e2e-job",
            device_id="large-simulator",
            shots=10,
            job_type="sampling",
            input="fake://input.zip",
            transpiler_info={},
            status=state["status"],
            simulator_info={"backend": "mpi-qulacs", "n_nodes": 1, "n_per_node": 1},
            mitigation_info={},
            ended_at=(
                datetime.fromisoformat(ended_at) if ended_at is not None else None
            ),
        )


def _write_outcome(path: Path, payload: dict[str, Any]) -> None:
    replace_json(path, payload)


async def _run(args: argparse.Namespace) -> None:
    execution_repository = OqtopusCloudExecutionRepository(
        device_id="large-simulator",
        work_root=str(args.work_root),
        jobs_api=FileJobsApi(args.cloud_state),
    )
    repository = FileJobRepository(args.cloud_state)
    process_lock = SingleProcessLock(args.process_lock)
    process_lock.acquire()
    try:
        await execution_repository.initialize()
        cloud_state = read_json(args.cloud_state)
        job = _job(cloud_state["status"])
        client = SlurmClient(
            partition="fake",
            command_timeout_seconds=5,
            controller_retry_interval_seconds=0,
        )
        if not await client.healthcheck():
            message = "fake SLURM partition is unavailable"
            raise RuntimeError(message)
        lifecycle_step = SimulatorLifecycleStep(
            execution_repository=execution_repository,
            job_reader=execution_repository,
            work_root=str(args.work_root),
            finalize_retry_count=0,
        )
        step = SlurmSimulatorStep(
            slurm_client=client,
            execution_repository=execution_repository,
            job_reader=execution_repository,
            work_root=str(args.work_root),
            batch_script=str(args.batch_script),
            worker_script=str(args.worker_script),
            poll_interval_seconds=0.05,
        )
        gctx = GlobalContext(config={}, job_repository=repository)
        jctx = JobContext()
        try:
            await lifecycle_step.pre_process(gctx, jctx, job)
            await step.pre_process(
                gctx,
                jctx,
                job,
            )
        except JobCancelledError as error:
            await SlurmPipelineExceptionHandler(
                execution_repository,
                execution_repository,
            ).handle_exception(error, gctx, jctx, job)
            record = await execution_repository.get(job.job_id)
            _write_outcome(
                args.outcome,
                {
                    "status": "cancelled",
                    "message": str(error),
                    "execution_state": (
                        record.state.value if record is not None else None
                    ),
                },
            )
            return

        await lifecycle_step.post_process(
            gctx,
            jctx,
            job,
        )
        record = await execution_repository.get(job.job_id)
        _write_outcome(
            args.outcome,
            {
                "status": "succeeded",
                "result": job.result.model_dump() if job.result is not None else None,
                "execution_time": job.execution_time,
                "execution_state": record.state.value if record is not None else None,
            },
        )
    finally:
        process_lock.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-lock", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--batch-script", type=Path, required=True)
    parser.add_argument("--worker-script", type=Path, required=True)
    parser.add_argument("--cloud-state", type=Path, required=True)
    parser.add_argument("--outcome", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except Exception as error:
        _write_outcome(
            args.outcome,
            {
                "status": "error",
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )
        raise


if __name__ == "__main__":
    main()
