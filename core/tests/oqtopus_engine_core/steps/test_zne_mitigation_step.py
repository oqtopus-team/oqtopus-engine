import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.framework import Job, JobContext, JobInfo
from oqtopus_engine_core.interfaces.mitigator_interface.v1 import mitigator_pb2
from oqtopus_engine_core.steps.zne_mitigation_step import ZneMitigationStep


def _build_job(job_type: str = "estimation", fail_open: bool = True) -> Job:
    return Job(
        job_id="job-zne",
        device_id="device-1",
        shots=100,
        job_type=job_type,
        job_info=JobInfo(program=['OPENQASM 3.0; include "stdgates.inc";']),
        transpiler_info={},
        simulator_info={},
        mitigation_info={"zne": {"enabled": True, "fail_open": fail_open}},
        status="ready",
    )


@pytest.mark.asyncio
async def test_pre_process_calls_req_zne_pre_process() -> None:
    step = ZneMitigationStep(mitigator_timeout_seconds=5)
    step._stub = AsyncMock()
    step._stub.ReqZnePreProcess = AsyncMock(
        return_value=mitigator_pb2.ReqZnePreProcessResponse(
            execution_programs=[
                mitigator_pb2.ZneExecutionProgram(
                    scale_factor=1.0,
                    repetition=0,
                    program_index=0,
                    suffix="s1-r0-p0",
                    program="folded",
                )
            ]
        )
    )

    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        preprocessed_qasms=['OPENQASM 3.0; include "stdgates.inc";'],
        grouped_operators=[[['Z']], [[1.0]]],
        counts_list=None,
    )

    await step.pre_process(SimpleNamespace(), jctx, _build_job())

    assert step._stub.ReqZnePreProcess.await_count == 1
    assert "zne_job_info" in jctx
    assert len(jctx["zne_job_info"]["execution_programs"]) == 1


@pytest.mark.asyncio
async def test_sampling_with_zne_is_skipped_when_fail_open_true() -> None:
    step = ZneMitigationStep()
    jctx = JobContext()

    await step.pre_process(SimpleNamespace(), jctx, _build_job(job_type="sampling", fail_open=True))

    assert "zne_job_info" not in jctx


@pytest.mark.asyncio
async def test_sampling_with_zne_raises_when_fail_open_false() -> None:
    step = ZneMitigationStep()
    with pytest.raises(ValueError, match="supported only for estimation"):
        await step.pre_process(
            SimpleNamespace(),
            JobContext(),
            _build_job(job_type="sampling", fail_open=False),
        )


@pytest.mark.asyncio
async def test_grouped_operators_none_behavior() -> None:
    step = ZneMitigationStep()
    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        preprocessed_qasms=['OPENQASM 3.0; include "stdgates.inc";'],
        grouped_operators=None,
        counts_list=None,
    )

    await step.pre_process(SimpleNamespace(), jctx, _build_job(fail_open=True))
    assert "zne_job_info" not in jctx

    with pytest.raises(ValueError, match="grouped_operators is None"):
        await step.pre_process(SimpleNamespace(), jctx, _build_job(fail_open=False))


@pytest.mark.asyncio
async def test_post_process_calls_req_zne_post_process_and_updates_result() -> None:
    step = ZneMitigationStep(mitigator_timeout_seconds=5)
    step._stub = AsyncMock()
    step._stub.ReqZnePostProcess = AsyncMock(
        return_value=mitigator_pb2.ReqZnePostProcessResponse(
            exp_value=1.23,
            stds=0.45,
            metadata_json=json.dumps({"ok": True}),
        )
    )

    jctx = JobContext()
    jctx["zne_job_info"] = {
        "grouped_operators_json": json.dumps([[['Z']], [[1.0]]]),
        "zne_config_json": json.dumps({"enabled": True}),
        "execution_results": [
            {
                "scale_factor": 1.0,
                "repetition": 0,
                "program_index": 0,
                "counts": {"0": 90, "1": 10},
            }
        ],
    }
    job = _build_job()

    await step.post_process(SimpleNamespace(), jctx, job)

    assert step._stub.ReqZnePostProcess.await_count == 1
    post_request = step._stub.ReqZnePostProcess.await_args.args[0]
    assert len(post_request.execution_results) == 1
    assert post_request.execution_results[0].counts["0"] == 90
    assert job.job_info.result is not None
    assert job.job_info.result.estimation is not None
    assert job.job_info.result.estimation.exp_value == 1.23
    assert job.job_info.result.estimation.stds == 0.45

