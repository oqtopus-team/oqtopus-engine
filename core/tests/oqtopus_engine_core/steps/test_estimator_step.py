import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.buffers import QueueBuffer
from oqtopus_engine_core.framework import (
    Job,
    JobContext,
    JobInfo,
    JobResult,
    OperatorItem,
    PipelineExecutor,
    SamplingResult,
    Step,
)
from oqtopus_engine_core.framework.context import GlobalContext
from oqtopus_engine_core.framework.pipeline import StepPhase
from oqtopus_engine_core.steps.estimator_step import (
    ESTIMATION_CHILD_INDEX_KEY,
    ESTIMATION_JOIN_INFO_KEY,
    EstimationJoinInfo,
    EstimatorStep,
)


def _make_estimation_job(job_id: str = "job-1") -> Job:
    return Job(
        job_id=job_id,
        job_type="estimation",
        device_id="device-1",
        shots=100,
        job_info=JobInfo(
            program=["OPENQASM 3.0;\n"],
            operator=[OperatorItem(pauli="X 0", coeff=1.0)],
        ),
        transpiler_info={},
        simulator_info={},
        mitigation_info={"ro_error_mitigation": "pseudo_inverse"},
        status="submitted",
    )


@pytest.fixture
def estimator_step_instance() -> EstimatorStep:
    step = EstimatorStep(basis_gates=["cx", "rz", "sx", "measure"])
    step._stub = MagicMock()
    step._stub.ReqEstimationPreProcess = AsyncMock()
    step._stub.ReqEstimationPostProcess = AsyncMock()
    return step


@pytest.mark.asyncio
async def test_pre_process_calls_grpc_and_creates_children(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    jctx = JobContext(initial={})
    job = _make_estimation_job()

    estimator_step_instance._stub.ReqEstimationPreProcess.return_value = SimpleNamespace(
        qasm_codes=["preprocessed-qasm-0", "preprocessed-qasm-1"],
        grouped_operators=json.dumps([[["X"]], [[1.0]]]),
    )

    await estimator_step_instance.pre_process(gctx, jctx, job)

    estimator_step_instance._stub.ReqEstimationPreProcess.assert_awaited_once()
    request = estimator_step_instance._stub.ReqEstimationPreProcess.call_args.args[0]
    assert request.qasm_code == "OPENQASM 3.0;\n"
    assert request.operators == "[('X 0', 1.0)]"
    assert list(request.basis_gates) == ["cx", "rz", "sx", "measure"]
    assert list(request.mapping_list) == []

    join_info = jctx[ESTIMATION_JOIN_INFO_KEY]
    assert isinstance(join_info, EstimationJoinInfo)
    assert join_info.grouped_operators == [[["X"]], [[1.0]]]
    assert len(job.children) == 2
    assert len(jctx.children) == 2
    assert join_info.child_order == [child.job_id for child in job.children]
    assert all(child.job_type == "sampling" for child in job.children)
    assert "has_actual_children" not in jctx.children[0]
    assert jctx.children[0]["has_actual_parent"] is True
    assert jctx.children[0][ESTIMATION_CHILD_INDEX_KEY] == 0
    assert jctx.children[1][ESTIMATION_CHILD_INDEX_KEY] == 1


@pytest.mark.asyncio
async def test_pre_process_uses_transpile_mapping_in_sorted_order(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    jctx = JobContext(initial={})
    job = _make_estimation_job("job-2")
    job.job_info.program = ["unused"]
    job.job_info.transpile_result = SimpleNamespace(
        transpiled_program="TRANSPILED",
        virtual_physical_mapping={"qubit_mapping": {"1": 0, "0": 2}},
    )

    estimator_step_instance._stub.ReqEstimationPreProcess.return_value = SimpleNamespace(
        qasm_codes=["qasm"],
        grouped_operators=json.dumps([[["Z"]], [[2.0]]]),
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
    jctx = JobContext(initial={})
    job = _make_estimation_job("job-3")
    job.job_info.operator = None

    with pytest.raises(ValueError, match="operator is not specified"):
        await estimator_step_instance.pre_process(gctx, jctx, job)


@pytest.mark.asyncio
async def test_join_jobs_calls_grpc_and_updates_parent_result(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    parent_job = _make_estimation_job("job-4")
    parent_job.children = [
        Job(
            job_id="job-4-1",
            job_type="sampling",
            device_id="device-1",
            shots=100,
            job_info=JobInfo(
                program=["qasm-1"],
                result=JobResult(sampling=SamplingResult(counts={"11": 20, "00": 10})),
                message="child-1-message",
            ),
            transpiler_info={},
            simulator_info={},
            mitigation_info={},
            status="running",
            execution_time=0.4,
        ),
        Job(
            job_id="job-4-0",
            job_type="sampling",
            device_id="device-1",
            shots=100,
            job_info=JobInfo(
                program=["qasm-0"],
                result=JobResult(sampling=SamplingResult(counts={"01": 5, "10": 7})),
                message="child-0-message",
            ),
            transpiler_info={},
            simulator_info={},
            mitigation_info={},
            status="running",
            execution_time=0.3,
        ),
    ]

    join_info = EstimationJoinInfo()
    join_info.grouped_operators = [[["ZZ"]], [[1.0]]]
    join_info.child_order = [
        "job-4-0",
        "job-4-1",
    ]
    join_info.started_at = time.perf_counter() - 0.25
    parent_jctx = JobContext(initial={ESTIMATION_JOIN_INFO_KEY: join_info})

    estimator_step_instance._stub.ReqEstimationPostProcess.return_value = SimpleNamespace(
        expval=0.25,
        stds=0.05,
    )

    await estimator_step_instance.join_jobs(
        gctx=gctx,
        parent_jctx=parent_jctx,
        parent_job=parent_job,
        last_child=parent_job.children[1],
    )

    request = estimator_step_instance._stub.ReqEstimationPostProcess.call_args.args[0]
    assert len(request.counts) == 2
    assert dict(request.counts[0].counts) == {"01": 5, "10": 7}
    assert dict(request.counts[1].counts) == {"11": 20, "00": 10}
    assert json.loads(request.grouped_operators) == [[["ZZ"]], [[1.0]]]
    assert parent_job.job_info.result.estimation.exp_value == 0.25
    assert parent_job.job_info.result.estimation.stds == 0.05
    assert parent_job.execution_time == 0.7
    assert parent_job.job_info.message == "child-0-message"


class FakeSamplingExecutionStep(Step):
    async def pre_process(self, gctx, jctx, job):
        """Populate counts for internal sampling children."""

    async def post_process(self, gctx, jctx, job):
        if (
            jctx.get("has_actual_parent", False)
        ):
            index = jctx[ESTIMATION_CHILD_INDEX_KEY]
            job.job_info.result = JobResult(
                sampling=SamplingResult(counts={f"{index}": index + 1})
            )
            job.job_info.message = f"child-{index}"


@pytest.mark.asyncio
async def test_same_step_split_and_join_flow() -> None:
    estimator_step = EstimatorStep()
    estimator_step._stub = MagicMock()
    estimator_step._stub.ReqEstimationPreProcess = AsyncMock(
        return_value=SimpleNamespace(
            qasm_codes=["qasm-0", "qasm-1"],
            grouped_operators=json.dumps([[["X"]], [[1.0]]]),
        )
    )
    estimator_step._stub.ReqEstimationPostProcess = AsyncMock(
        return_value=SimpleNamespace(expval=0.75, stds=0.125)
    )

    pipeline = [estimator_step, FakeSamplingExecutionStep()]
    executor = PipelineExecutor(pipeline, QueueBuffer())
    parent_job = _make_estimation_job("root")
    parent_jctx = JobContext(initial={})

    await executor._run_from(
        StepPhase.PRE_PROCESS,
        0,
        GlobalContext(config={}),
        parent_jctx,
        parent_job,
    )

    assert parent_job.job_info.result.estimation.exp_value == 0.75
    assert parent_job.job_info.result.estimation.stds == 0.125
    assert parent_job.job_info.message == "child-1"
    assert parent_jctx.step_history == [
        ("pre_process", 0),
    ]
    for child_jctx in parent_jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("post_process", 1),
            ("post_process", 0),
        ]


@pytest.mark.asyncio
async def test_non_estimation_jobs_are_skipped(
    estimator_step_instance: EstimatorStep,
) -> None:
    gctx = MagicMock()
    jctx = JobContext(initial={})
    job = Job(
        job_id="job-5",
        job_type="sampling",
        device_id="device-1",
        shots=100,
        job_info=JobInfo(program=["OPENQASM 3.0;\n"]),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="submitted",
    )

    await estimator_step_instance.pre_process(gctx, jctx, job)
    await estimator_step_instance.post_process(gctx, jctx, job)

    estimator_step_instance._stub.ReqEstimationPreProcess.assert_not_awaited()
    estimator_step_instance._stub.ReqEstimationPostProcess.assert_not_awaited()
