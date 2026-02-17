import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.framework import (
    EstimationResult,
    Job,
    JobContext,
    JobInfo,
    JobResult,
    OperatorItem,
)
from oqtopus_engine_core.interfaces.estimator_interface.v1 import estimator_pb2
from oqtopus_engine_core.steps.estimator_step import DEFAULT_BASIS_GATES, EstimatorStep


def _build_job(job_type: str = "estimation") -> Job:
    return Job(
        job_id="job-estimator",
        device_id="device-1",
        shots=100,
        job_type=job_type,
        job_info=JobInfo(
            program=['OPENQASM 3.0; include "stdgates.inc";'],
            operator=[OperatorItem(pauli="Z 0", coeff=1.0)],
        ),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
    )


def test_init_uses_default_basis_gates_when_none() -> None:
    step = EstimatorStep(basis_gates=None)
    assert step._basis_gates == DEFAULT_BASIS_GATES


def test_init_uses_given_basis_gates() -> None:
    step = EstimatorStep(basis_gates=["cx", "rz", "measure"])
    assert step._basis_gates == ["cx", "rz", "measure"]


@pytest.mark.asyncio
async def test_pre_process_skips_non_estimation_job() -> None:
    step = EstimatorStep()
    step._stub = AsyncMock()
    step._stub.ReqEstimationPreProcess = AsyncMock()

    await step.pre_process(SimpleNamespace(), JobContext(), _build_job(job_type="sampling"))

    assert step._stub.ReqEstimationPreProcess.await_count == 0


@pytest.mark.asyncio
async def test_pre_process_calls_grpc_and_sets_estimation_job_info() -> None:
    step = EstimatorStep(basis_gates=["cx", "rz", "measure"])
    step._stub = AsyncMock()
    step._stub.ReqEstimationPreProcess = AsyncMock(
        return_value=estimator_pb2.ReqEstimationPreProcessResponse(
            qasm_codes=['OPENQASM 3.0; include "stdgates.inc"; bit[1] c; c[0] = measure $0;'],
            grouped_operators=json.dumps([[['Z']], [[1.0]]]),
        )
    )

    jctx = JobContext()
    job = _build_job()
    job.job_info.transpile_result = None

    await step.pre_process(SimpleNamespace(), jctx, job)

    assert step._stub.ReqEstimationPreProcess.await_count == 1
    request = step._stub.ReqEstimationPreProcess.await_args.args[0]
    assert request.basis_gates == ["cx", "rz", "measure"]
    assert request.mapping_list == []
    assert "Z 0" in request.operators

    assert "estimation_job_info" in jctx
    assert jctx["estimation_job_info"].preprocessed_qasms
    assert jctx["estimation_job_info"].grouped_operators == [[['Z']], [[1.0]]]


@pytest.mark.asyncio
async def test_pre_process_raises_without_operator() -> None:
    step = EstimatorStep()
    step._stub = AsyncMock()

    job = _build_job()
    job.job_info.operator = None

    with pytest.raises(ValueError, match="the operator is not specified in the job"):
        await step.pre_process(SimpleNamespace(), JobContext(), job)


@pytest.mark.asyncio
async def test_post_process_calls_grpc_and_sets_result() -> None:
    step = EstimatorStep()
    step._stub = AsyncMock()
    step._stub.ReqEstimationPostProcess = AsyncMock(
        return_value=estimator_pb2.ReqEstimationPostProcessResponse(
            expval=0.123,
            stds=0.045,
        )
    )

    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        counts_list=[{"0": 90, "1": 10}],
        grouped_operators=[[['Z']], [[1.0]]],
    )
    job = _build_job()

    await step.post_process(SimpleNamespace(), jctx, job)

    assert step._stub.ReqEstimationPostProcess.await_count == 1
    assert job.job_info.result is not None
    assert job.job_info.result.estimation is not None
    assert job.job_info.result.estimation.exp_value == pytest.approx(0.123)
    assert job.job_info.result.estimation.stds == pytest.approx(0.045)


@pytest.mark.asyncio
async def test_post_process_skips_when_precomputed_result_exists() -> None:
    step = EstimatorStep()
    step._stub = AsyncMock()
    step._stub.ReqEstimationPostProcess = AsyncMock()

    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        counts_list=[{"0": 90, "1": 10}],
        grouped_operators=[[['Z']], [[1.0]]],
    )
    job = _build_job()
    job.job_info.result = JobResult(estimation=EstimationResult(exp_value=1.0, stds=0.0))

    await step.post_process(SimpleNamespace(), jctx, job)

    assert step._stub.ReqEstimationPostProcess.await_count == 0
