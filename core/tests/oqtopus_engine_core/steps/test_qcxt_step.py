import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.buffers import QueueBuffer
from oqtopus_engine_core.framework import (
    EstimationResult,
    Job,
    JobContext,
    JobResult,
    OperatorItem,
    PipelineDirective,
    PipelineExecutor,
    Step,
    StepResult,
)
from oqtopus_engine_core.framework.context import GlobalContext
from oqtopus_engine_core.framework.model import TranspileResult
from oqtopus_engine_core.framework.pipeline import StepPhase
from oqtopus_engine_core.steps.qcxt_step import (
    QCXT_CHILD_INDEX_KEY,
    QCXT_JOIN_INFO_KEY,
    QcxtJoinInfo,
    QcxtStep,
)

DUMMY_SHOTS = 100
DUMMY_PROGRAM = "OPENQASM 3.0;\n"
DUMMY_BASIS_GATES = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
DUMMY_SUB_PROGRAMS = ["sub-program-0", "sub-program-1", "sub-program-2", "sub-program-3"]
DUMMY_COEFFICIENTS = [0.5, 0.5, 0.5, -0.5]
DUMMY_VIRTUAL_COUPLINGS = [json.dumps({"control": 0, "target": 1, "fidelity": 0.9})]
DUMMY_SUB_EXPVALS = [0.2, -0.4, 0.6, -0.8]
DUMMY_SUB_STDS = [0.1, 0.3, 0.5, 0.7]
DUMMY_DEVICE_INFO = json.dumps(
    {
        "qubits": [{"id": 0, "physical_id": 12}, {"id": 1, "physical_id": 14}],
        "couplings": [{"control": 0, "target": 1, "fidelity": 0.9}],
    }
)
DUMMY_EXPVAL = 0.6
DUMMY_STDS = 0.1
DUMMY_EXECUTION_TIMES = [0.111, 0.222, 0.333, 0.444]
DUMMY_TRANSPILER_INFO = {
    "transpiler_options": {"initial_layout": [1, 0], "optimization_level": 3}
}
DUMMY_TRANSPILE_RESULT = TranspileResult(
    transpiled_program="transpiled-program",
    stats={"depth": 5},
    virtual_physical_mapping={"qubit_mapping": {"1": 0, "0": 2}},
)


def _make_qcxt_job(job_id: str = "job-1") -> Job:
    return Job(
        job_id=job_id,
        repository_job_id=job_id,
        job_type="estimation",
        device_id="device-1",
        shots=DUMMY_SHOTS,
        input=f"{job_id}/input.zip",
        program=[DUMMY_PROGRAM],
        operator=[OperatorItem(pauli="X 0", coeff=1.0)],
        transpiler_info={},
        simulator_info={},
        mitigation_info={"qcxt": True},
        status="submitted",
    )


def _make_step(*, enable_qcxt: bool = True) -> QcxtStep:
    step = QcxtStep(
        basis_gates=DUMMY_BASIS_GATES,
        enable_qcxt=enable_qcxt
    )
    step._stub = MagicMock()
    step._stub.ReqQcxtPreProcess = AsyncMock(
        return_value=SimpleNamespace(
            sub_programs=DUMMY_SUB_PROGRAMS,
            coefficients=DUMMY_COEFFICIENTS,
            virtual_couplings=DUMMY_VIRTUAL_COUPLINGS,
        )
    )
    step._stub.ReqQcxtPostProcess = AsyncMock(
        return_value=SimpleNamespace(exp_val=DUMMY_EXPVAL, stds=DUMMY_STDS)
    )
    return step


def _make_gctx() -> MagicMock:
    gctx = MagicMock()
    gctx.device.device_info = DUMMY_DEVICE_INFO
    return gctx


def _make_child(index: int, parent_id: str, *, with_result: bool = True) -> list[Job]:
    result = (
        JobResult(
            estimation=EstimationResult(
                exp_value=DUMMY_SUB_EXPVALS[index], stds=DUMMY_SUB_STDS[index]
            )
        )
        if with_result
        else None
    )
    child = Job(
        job_id=f"{parent_id}-qcxt-{index}",
        repository_job_id=parent_id,
        job_type="estimation",
        device_id="device-1",
        shots=DUMMY_SHOTS,
        input=f"{parent_id}/input.zip",
        program=[DUMMY_SUB_PROGRAMS[index]],
        result=result,
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="submitted",
        message=f"child-{index}-message",
        execution_time=DUMMY_EXECUTION_TIMES[index],
    )
    return child


def _make_children(parent_id: str, *, with_result: bool = True) -> list[Job]:
    children = []
    for i in range(len(DUMMY_SUB_PROGRAMS)):
        child = _make_child(index=i, parent_id=parent_id, with_result=with_result)
        children.append(child)
    return children


def _make_join_info(parent_id: str) -> QcxtJoinInfo:
    join_info = QcxtJoinInfo()
    join_info.coefficients = DUMMY_COEFFICIENTS
    join_info.child_order = [f"{parent_id}-qcxt-{i}" for i in range(len(DUMMY_SUB_PROGRAMS))]
    return join_info


@pytest.fixture
def step() -> QcxtStep:
    return _make_step()


# ---------------------------------------------------------------------------
# pre_process
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_process_splits_into_children(step: QcxtStep) -> None:
    jctx = JobContext(initial={})
    job = _make_qcxt_job("job-1")

    result = await step.pre_process(_make_gctx(), jctx, job)

    step._stub.ReqQcxtPreProcess.assert_awaited_once()
    request = step._stub.ReqQcxtPreProcess.call_args.args[0]
    assert request.program == DUMMY_PROGRAM
    assert request.device_info == DUMMY_DEVICE_INFO
    assert request.shots == DUMMY_SHOTS
    assert list(request.basis_gates) == DUMMY_BASIS_GATES

    assert result.directive == PipelineDirective.SPLIT_FOR_JOIN
    assert result.child_jobs is not None
    assert result.child_contexts is not None
    assert len(result.child_jobs) == len(result.child_contexts) == len(DUMMY_SUB_PROGRAMS)

    assert job.result is not None
    assert job.result.mitigation_details is not None
    assert job.result.mitigation_details.qcxt is not None
    assert job.result.mitigation_details.qcxt.virtual_couplings == [
        json.loads(vc) for vc in DUMMY_VIRTUAL_COUPLINGS
    ]

    join_info = jctx[QCXT_JOIN_INFO_KEY]
    assert isinstance(join_info, QcxtJoinInfo)
    assert list(join_info.coefficients) == DUMMY_COEFFICIENTS
    assert join_info.child_order == [f"job-1-qcxt-{i}" for i in range(len(DUMMY_SUB_PROGRAMS))]
    assert join_info.started_at is not None

    for i, (child, child_ctx) in enumerate(
        zip(result.child_jobs, result.child_contexts, strict=True)
    ):
        assert child.job_id == f"{job.job_id}-qcxt-{i}"
        # The child is an estimation job running one sub-program.
        assert child.repository_job_id == job.repository_job_id
        assert child.device_id == job.device_id
        assert child.shots == job.shots
        assert child.job_type == "estimation"
        assert child.program == [DUMMY_SUB_PROGRAMS[i]]
        assert child.operator == job.operator
        assert child.transpile_result is None
        assert child.mitigation_info == job.mitigation_info
        assert child.result is not None
        assert child.result.estimation is not None
        assert child_ctx[QCXT_CHILD_INDEX_KEY] == i


@pytest.mark.asyncio
async def test_pre_process_uses_transpiled_program_and_updates_children(
    step: QcxtStep,
) -> None:
    jctx = JobContext(initial={})
    job = _make_qcxt_job("job-2")
    job.transpiler_info = DUMMY_TRANSPILER_INFO
    job.transpile_result = DUMMY_TRANSPILE_RESULT

    result = await step.pre_process(_make_gctx(), jctx, job)

    request = step._stub.ReqQcxtPreProcess.call_args.args[0]
    assert request.program == DUMMY_TRANSPILE_RESULT.transpiled_program

    assert result.child_jobs is not None
    for i, child in enumerate(result.child_jobs):
        assert child.transpile_result is not None
        assert child.transpile_result.transpiled_program == DUMMY_SUB_PROGRAMS[i]
        assert child.transpile_result.stats == {}
        assert child.transpile_result.virtual_physical_mapping == DUMMY_TRANSPILE_RESULT.virtual_physical_mapping
        # initial_layout is cleared so that mp_auto is disabled for children.
        assert child.transpiler_info["transpiler_options"]["initial_layout"] is None
        assert child.transpiler_info["transpiler_options"]["optimization_level"] == DUMMY_TRANSPILER_INFO["transpiler_options"]["optimization_level"]


@pytest.mark.asyncio
async def test_pre_process_raises_when_qcxt_designated_but_disabled() -> None:
    step = _make_step(enable_qcxt=False)
    jctx = JobContext(initial={})

    with pytest.raises(ValueError, match="qcxt is set, but qCxT is disabled."):
        await step.pre_process(_make_gctx(), jctx, _make_qcxt_job("job-5"))

    step._stub.ReqQcxtPreProcess.assert_not_awaited()
    assert QCXT_JOIN_INFO_KEY not in jctx


@pytest.mark.asyncio
async def test_pre_process_skips_when_disabled_and_qcxt_not_designated() -> None:
    step = _make_step(enable_qcxt=False)
    jctx = JobContext(initial={})
    job = _make_qcxt_job("job-6")
    job.mitigation_info = {}

    result = await step.pre_process(_make_gctx(), jctx, job)

    assert result.directive == PipelineDirective.NONE
    step._stub.ReqQcxtPreProcess.assert_not_awaited()
    assert QCXT_JOIN_INFO_KEY not in jctx


@pytest.mark.asyncio
async def test_pre_process_skips_non_estimation_job(step: QcxtStep) -> None:
    jctx = JobContext(initial={})
    job = _make_qcxt_job("job-7")
    job.job_type = "sampling"

    result = await step.pre_process(_make_gctx(), jctx, job)

    assert result.directive == PipelineDirective.NONE
    step._stub.ReqQcxtPreProcess.assert_not_awaited()
    assert QCXT_JOIN_INFO_KEY not in jctx


@pytest.mark.parametrize("mitigation_info", [{}, {"qcxt": False}])
@pytest.mark.asyncio
async def test_pre_process_skips_when_qcxt_not_designated(
    step: QcxtStep, mitigation_info: dict
) -> None:
    jctx = JobContext(initial={})
    job = _make_qcxt_job("job-8")
    job.mitigation_info = mitigation_info

    result = await step.pre_process(_make_gctx(), jctx, job)

    assert result.directive == PipelineDirective.NONE
    step._stub.ReqQcxtPreProcess.assert_not_awaited()
    assert QCXT_JOIN_INFO_KEY not in jctx


@pytest.mark.asyncio
async def test_pre_process_skips_for_qcxt_child(step: QcxtStep) -> None:
    jctx = JobContext(initial={QCXT_CHILD_INDEX_KEY: 0})
    job = _make_qcxt_job("job-9-qcxt-0")

    result = await step.pre_process(_make_gctx(), jctx, job)

    assert result.directive == PipelineDirective.NONE
    step._stub.ReqQcxtPreProcess.assert_not_awaited()
    assert QCXT_JOIN_INFO_KEY not in jctx


# ---------------------------------------------------------------------------
# post_process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_process_signals_join_for_child(step: QcxtStep) -> None:
    jctx = JobContext(initial={QCXT_CHILD_INDEX_KEY: 1})
    child = _make_child(index=1, parent_id="job-10")
    child.status = "running"

    result = await step.post_process(_make_gctx(), jctx, child)

    assert result.directive == PipelineDirective.JOIN
    assert child.status == "succeeded"


@pytest.mark.asyncio
async def test_post_process_does_not_overwrite_already_failed_child(
    step: QcxtStep,
) -> None:
    jctx = JobContext(initial={QCXT_CHILD_INDEX_KEY: 2})
    child = _make_child(index=2, parent_id="job-11")
    child.status = "failed"

    result = await step.post_process(_make_gctx(), jctx, child)

    assert result.directive == PipelineDirective.JOIN
    assert child.status == "failed"


@pytest.mark.asyncio
async def test_post_process_ignores_non_child_job(step: QcxtStep) -> None:
    job = _make_qcxt_job("job-12")
    job.status = "running"

    result = await step.post_process(_make_gctx(), JobContext(initial={}), job)

    assert result.directive == PipelineDirective.NONE
    assert job.status == "running"


# ---------------------------------------------------------------------------
# join_jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_jobs_updates_parent_result(step: QcxtStep) -> None:
    parent_job = _make_qcxt_job("job-13")
    parent_job.children = _make_children("job-13")
    parent_jctx = JobContext(initial={QCXT_JOIN_INFO_KEY: _make_join_info("job-13")})

    await step.join_jobs(
        gctx=_make_gctx(),
        parent_jctx=parent_jctx,
        parent_job=parent_job,
        last_child=parent_job.children[-1],
    )

    step._stub.ReqQcxtPostProcess.assert_awaited_once()
    request = step._stub.ReqQcxtPostProcess.call_args.args[0]
    assert list(request.sub_expvals) == pytest.approx(DUMMY_SUB_EXPVALS)
    assert list(request.sub_stds) == pytest.approx(DUMMY_SUB_STDS)
    assert list(request.coefficients) == pytest.approx(DUMMY_COEFFICIENTS)

    assert parent_job.result is not None
    assert parent_job.result.estimation is not None
    assert parent_job.result.estimation.exp_value == DUMMY_EXPVAL
    assert parent_job.result.estimation.stds == DUMMY_STDS
    assert parent_job.execution_time == pytest.approx(sum(DUMMY_EXECUTION_TIMES))
    assert parent_job.message == parent_job.children[-1].message


@pytest.mark.asyncio
async def test_join_jobs_follows_child_order(step: QcxtStep) -> None:
    parent_job = _make_qcxt_job("job-14")
    children = _make_children("job-14")
    parent_job.children = list(reversed(children))
    parent_jctx = JobContext(initial={QCXT_JOIN_INFO_KEY: _make_join_info("job-14")})

    await step.join_jobs(
        gctx=_make_gctx(),
        parent_jctx=parent_jctx,
        parent_job=parent_job,
        last_child=children[-1],
    )

    request = step._stub.ReqQcxtPostProcess.call_args.args[0]
    assert list(request.sub_expvals) == pytest.approx(DUMMY_SUB_EXPVALS)
    assert list(request.sub_stds) == pytest.approx(DUMMY_SUB_STDS)
    assert parent_job.message == parent_job.children[0].message


@pytest.mark.asyncio
async def test_join_jobs_raises_when_join_info_missing(step: QcxtStep) -> None:
    parent_job = _make_qcxt_job("job-15")
    parent_job.children = _make_children("job-15")

    with pytest.raises(RuntimeError, match="qcxt_join_info is not initialized"):
        await step.join_jobs(
            gctx=_make_gctx(),
            parent_jctx=JobContext(initial={}),
            parent_job=parent_job,
            last_child=parent_job.children[-1],
        )

    step._stub.ReqQcxtPostProcess.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_jobs_raises_when_child_estimation_missing(step: QcxtStep) -> None:
    parent_job = _make_qcxt_job("job-16")
    parent_job.children = _make_children("job-16")
    parent_job.children[0].result = JobResult()
    parent_jctx = JobContext(initial={QCXT_JOIN_INFO_KEY: _make_join_info("job-16")})

    with pytest.raises(RuntimeError, match=f"child estimation result not found during join: {parent_job.children[0].job_id}"):
        await step.join_jobs(
            gctx=_make_gctx(),
            parent_jctx=parent_jctx,
            parent_job=parent_job,
            last_child=parent_job.children[-1],
        )


# ---------------------------------------------------------------------------
# split -> join flow through PipelineExecutor
# ---------------------------------------------------------------------------


class FakeEstimationExecutionStep(Step):
    """Stand-in for the execution steps: fills a result for qcxt children."""

    async def pre_process(self, gctx, jctx, job):  # noqa: ANN001, ANN201
        return StepResult()

    async def post_process(self, gctx, jctx, job):  # noqa: ANN001, ANN201
        if QCXT_CHILD_INDEX_KEY in jctx:
            index = jctx[QCXT_CHILD_INDEX_KEY]
            job.result = JobResult(
                estimation=EstimationResult(
                    exp_value=DUMMY_SUB_EXPVALS[index], stds=DUMMY_SUB_STDS[index]
                )
            )
            job.message = f"child-{index}"
            job.execution_time = DUMMY_EXECUTION_TIMES[index]
        return StepResult()


@pytest.mark.asyncio
async def test_split_and_join_flow(step: QcxtStep) -> None:
    executor = PipelineExecutor([step, FakeEstimationExecutionStep()], QueueBuffer())
    parent_job = _make_qcxt_job("job-17")
    parent_jctx = JobContext(initial={})
    gctx = GlobalContext(config={})
    gctx.device = SimpleNamespace(device_info=DUMMY_DEVICE_INFO)

    await executor._run_from(StepPhase.PRE_PROCESS, 0, gctx, parent_jctx, parent_job)

    request = step._stub.ReqQcxtPostProcess.call_args.args[0]
    assert list(request.sub_expvals) == pytest.approx(DUMMY_SUB_EXPVALS)
    assert list(request.sub_stds) == pytest.approx(DUMMY_SUB_STDS)
    assert list(request.coefficients) == pytest.approx(DUMMY_COEFFICIENTS)

    assert parent_job.result is not None
    assert parent_job.result.estimation is not None
    assert parent_job.result.estimation.exp_value == DUMMY_EXPVAL
    assert parent_job.result.estimation.stds == DUMMY_STDS
    assert parent_job.execution_time == pytest.approx(sum(DUMMY_EXECUTION_TIMES))
    assert parent_job.message == f"child-{len(DUMMY_SUB_PROGRAMS) - 1}"
    assert parent_jctx.step_history == [("pre_process", 0)]
    assert len(parent_job.children) == len(DUMMY_SUB_PROGRAMS)
    assert all(child.status == "succeeded" for child in parent_job.children)
    for child_jctx in parent_jctx.children:
        assert child_jctx.step_history == [
            ("pre_process", 1),
            ("post_process", 1),
            ("post_process", 0),
        ]
