from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.framework import Job, JobContext, JobInfo
from oqtopus_engine_core.interfaces.mitigator_interface.v1 import mitigator_pb2
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2
from oqtopus_engine_core.steps.device_gateway_step import DeviceGatewayStep


def _build_job() -> Job:
    return Job(
        job_id="job-zne",
        device_id="device-1",
        shots=100,
        job_type="estimation",
        job_info=JobInfo(program=['OPENQASM 3.0; include "stdgates.inc";']),
        transpiler_info={},
        simulator_info={},
        mitigation_info={"zne": {"enabled": True, "fail_open": False}},
        status="ready",
    )


@pytest.mark.asyncio
async def test_estimation_with_zne_executes_folded_programs_on_qpu() -> None:
    step = DeviceGatewayStep()
    step._stub = AsyncMock()
    step._stub.GetServiceStatus.return_value = qpu_pb2.GetServiceStatusResponse(
        service_status=qpu_pb2.ServiceStatus.SERVICE_STATUS_ACTIVE
    )
    step._stub.CallJob = AsyncMock(
        side_effect=[
            qpu_pb2.CallJobResponse(
                status=qpu_pb2.JobStatus.JOB_STATUS_SUCCESS,
                result=qpu_pb2.Result(counts={"0": 90, "1": 10}, message="ok-1"),
            ),
            qpu_pb2.CallJobResponse(
                status=qpu_pb2.JobStatus.JOB_STATUS_SUCCESS,
                result=qpu_pb2.Result(counts={"0": 88, "1": 12}, message="ok-2"),
            ),
        ]
    )

    gctx = SimpleNamespace(job_repository=SimpleNamespace(update_job_status=AsyncMock()))
    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        preprocessed_qasms=["fallback-qasm"],
        grouped_operators=[[['Z']], [[1.0]]],
        counts_list=None,
    )
    jctx["zne_job_info"] = {
        "execution_programs": [
            mitigator_pb2.ZneExecutionProgram(
                scale_factor=1.0,
                repetition=0,
                program_index=0,
                suffix="s1-r0-p0",
                program="folded-1",
            ),
            mitigator_pb2.ZneExecutionProgram(
                scale_factor=2.0,
                repetition=0,
                program_index=0,
                suffix="s2-r0-p0",
                program="folded-2",
            ),
        ],
        "execution_results": [],
        "fail_open": False,
    }
    job = _build_job()

    await step.pre_process(gctx, jctx, job)

    assert step._stub.CallJob.await_count == 2
    assert len(jctx["zne_job_info"]["execution_results"]) == 2
    assert isinstance(jctx["zne_job_info"]["execution_results"][0], dict)
    assert jctx["zne_job_info"]["execution_results"][0]["counts"] == {"0": 90, "1": 10}
    assert jctx["estimation_job_info"].counts_list is None


@pytest.mark.asyncio
async def test_estimation_with_zne_fail_open_falls_back_to_direct_estimation() -> None:
    step = DeviceGatewayStep()
    step._stub = AsyncMock()
    step._stub.GetServiceStatus.return_value = qpu_pb2.GetServiceStatusResponse(
        service_status=qpu_pb2.ServiceStatus.SERVICE_STATUS_ACTIVE
    )
    step._stub.CallJob = AsyncMock(
        side_effect=[
            qpu_pb2.CallJobResponse(
                status=qpu_pb2.JobStatus.JOB_STATUS_FAILURE,
                result=qpu_pb2.Result(counts={}, message="zne failed"),
            ),
            qpu_pb2.CallJobResponse(
                status=qpu_pb2.JobStatus.JOB_STATUS_SUCCESS,
                result=qpu_pb2.Result(counts={"0": 80, "1": 20}, message="ok-fallback"),
            ),
        ]
    )

    gctx = SimpleNamespace(job_repository=SimpleNamespace(update_job_status=AsyncMock()))
    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        preprocessed_qasms=["direct-estimation-qasm"],
        grouped_operators=[[['Z']], [[1.0]]],
        counts_list=None,
    )
    jctx["zne_job_info"] = {
        "execution_programs": [
            mitigator_pb2.ZneExecutionProgram(
                scale_factor=2.0,
                repetition=0,
                program_index=0,
                suffix="s2-r0-p0",
                program="folded-error",
            )
        ],
        "execution_results": [],
        "fail_open": True,
    }
    job = _build_job()
    job.mitigation_info = {"zne": {"enabled": True, "fail_open": True}}

    await step.pre_process(gctx, jctx, job)

    assert step._stub.CallJob.await_count == 2
    assert jctx["zne_job_info"]["execution_results"] == []
    assert jctx["estimation_job_info"].counts_list == [{"0": 80, "1": 20}]
