import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.framework.model import OperatorItem
from oqtopus_engine_core.steps.estimator_step import EstimationJobInfo, EstimatorStep


@pytest.fixture
def estimator_step_instance() -> EstimatorStep:
    step = EstimatorStep(basis_gates=["cx", "rz", "sx", "measure"])
    step._stub = MagicMock()
    step._stub.ReqEstimationPreProcess = AsyncMock()
    step._stub.ReqEstimationPostProcess = AsyncMock()
    return step


@pytest.mark.asyncio
async def test_pre_process_calls_grpc_and_updates_jctx(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    jctx: dict[str, object] = {}
    job = MagicMock()
    job.job_id = "job-1"
    job.job_type = "estimation"
    job.transpile_result = None
    job.program = ["OPENQASM 3.0;\n"]
    job.operator = [OperatorItem(pauli="X 0", coeff=1.0)]

    estimator_step_instance._stub.ReqEstimationPreProcess.return_value = SimpleNamespace(
        qasm_codes=["preprocessed-qasm"],
        grouped_operators=json.dumps([[ ["X"] ], [ [1.0] ]]),
    )

    await estimator_step_instance.pre_process(gctx, jctx, job)

    assert "estimation_job_info" in jctx
    info = jctx["estimation_job_info"]
    assert isinstance(info, EstimationJobInfo)
    assert info.preprocessed_qasms == ["preprocessed-qasm"]
    assert info.grouped_operators == [[["X"]], [[1.0]]]

    estimator_step_instance._stub.ReqEstimationPreProcess.assert_awaited_once()
    request = estimator_step_instance._stub.ReqEstimationPreProcess.call_args.args[0]
    assert request.qasm_code == "OPENQASM 3.0;\n"
    assert request.operators == "[('X 0', 1.0)]"
    assert list(request.basis_gates) == ["cx", "rz", "sx", "measure"]
    assert list(request.mapping_list) == []


@pytest.mark.asyncio
async def test_pre_process_uses_transpile_mapping_in_sorted_order(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    jctx: dict[str, object] = {}
    job = MagicMock()
    job.job_id = "job-2"
    job.job_type = "estimation"
    job.program = ["unused"]
    job.operator = [OperatorItem(pauli="Z 1", coeff=2.0)]
    job.transpile_result = SimpleNamespace(
        transpiled_program="TRANSPILED",
        virtual_physical_mapping={"qubit_mapping": {"1": 0, "0": 2}},
    )

    estimator_step_instance._stub.ReqEstimationPreProcess.return_value = SimpleNamespace(
        qasm_codes=["qasm"],
        grouped_operators=json.dumps([[ ["Z"] ], [ [2.0] ]]),
    )

    await estimator_step_instance.pre_process(gctx, jctx, job)

    request = estimator_step_instance._stub.ReqEstimationPreProcess.call_args.args[0]
    assert request.qasm_code == "TRANSPILED"
    assert list(request.mapping_list) == [2, 0]


@pytest.mark.asyncio
async def test_pre_process_raises_when_operator_missing(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    jctx: dict[str, object] = {}
    job = MagicMock()
    job.job_id = "job-3"
    job.job_type = "estimation"
    job.transpile_result = None
    job.program = ["OPENQASM 3.0;\n"]
    job.operator = None

    with pytest.raises(ValueError, match="operator is not specified"):
        await estimator_step_instance.pre_process(gctx, jctx, job)


@pytest.mark.asyncio
async def test_post_process_calls_grpc_and_updates_job_result(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    job = MagicMock()
    job.job_id = "job-4"
    job.job_type = "estimation"
    job.result = None

    jctx = {
        "estimation_job_info": SimpleNamespace(
            counts_list=[{"00": 10, "11": 20}],
            grouped_operators=[[["ZZ"]], [[1.0]]],
        )
    }

    estimator_step_instance._stub.ReqEstimationPostProcess.return_value = SimpleNamespace(
        expval=0.25,
        stds=0.05,
    )

    await estimator_step_instance.post_process(gctx, jctx, job)

    estimator_step_instance._stub.ReqEstimationPostProcess.assert_awaited_once()
    request = estimator_step_instance._stub.ReqEstimationPostProcess.call_args.args[0]
    assert len(request.counts) == 1
    assert dict(request.counts[0].counts) == {"00": 10, "11": 20}
    assert json.loads(request.grouped_operators) == [[["ZZ"]], [[1.0]]]

    assert job.result is not None
    assert job.result.estimation is not None
    assert job.result.estimation.exp_value == 0.25
    assert job.result.estimation.stds == 0.05


@pytest.mark.asyncio
async def test_non_estimation_jobs_are_skipped(estimator_step_instance: EstimatorStep) -> None:
    gctx = MagicMock()
    jctx: dict[str, object] = {}
    job = MagicMock()
    job.job_id = "job-5"
    job.job_type = "sampling"

    await estimator_step_instance.pre_process(gctx, jctx, job)
    await estimator_step_instance.post_process(gctx, jctx, job)

    estimator_step_instance._stub.ReqEstimationPreProcess.assert_not_awaited()
    estimator_step_instance._stub.ReqEstimationPostProcess.assert_not_awaited()
