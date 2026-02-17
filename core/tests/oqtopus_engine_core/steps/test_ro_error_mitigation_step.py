import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.framework import (
    Job,
    JobContext,
    JobInfo,
    JobResult,
    SamplingResult,
)
from oqtopus_engine_core.interfaces.mitigator_interface.v1 import mitigator_pb2
from oqtopus_engine_core.steps.ro_error_mitigation_step import ReadoutErrorMitigationStep


def _build_gctx() -> SimpleNamespace:
    return SimpleNamespace(
        device=SimpleNamespace(
            device_info=json.dumps(
                {
                    "qubits": [
                        {"meas_error": {"prob_meas1_prep0": 0.01, "prob_meas0_prep1": 0.02}},
                        {"meas_error": {"prob_meas1_prep0": 0.03, "prob_meas0_prep1": 0.04}},
                    ]
                }
            )
        )
    )


@pytest.mark.asyncio
async def test_post_process_sampling_applies_readout_mitigation() -> None:
    step = ReadoutErrorMitigationStep(mitigator_timeout_seconds=5)
    step._stub = AsyncMock()
    step._stub.ReqMitigation = AsyncMock(
        return_value=mitigator_pb2.ReqMitigationResponse(
            counts={"00": 480, "01": 320, "10": 140, "11": 60}
        )
    )

    job = Job(
        job_id="job-ro-sampling",
        device_id="device-1",
        shots=1000,
        job_type="sampling",
        job_info=JobInfo(
            program=["OPENQASM 3.0; include \"stdgates.inc\";"],
            result=JobResult(
                sampling=SamplingResult(counts={"00": 500, "01": 300, "10": 150, "11": 50})
            ),
        ),
        transpiler_info={},
        simulator_info={},
        mitigation_info={"ro_error_mitigation": "pseudo_inverse"},
        status="ready",
    )

    await step.post_process(_build_gctx(), JobContext(), job)

    assert step._stub.ReqMitigation.await_count == 1
    assert job.job_info.result is not None
    assert job.job_info.result.sampling is not None
    assert job.job_info.result.sampling.counts == {
        "00": 480,
        "01": 320,
        "10": 140,
        "11": 60,
    }


@pytest.mark.asyncio
async def test_post_process_estimation_applies_readout_mitigation_to_counts_list() -> None:
    step = ReadoutErrorMitigationStep(mitigator_timeout_seconds=5)
    step._stub = AsyncMock()
    step._stub.ReqMitigation = AsyncMock(
        side_effect=[
            mitigator_pb2.ReqMitigationResponse(counts={"0": 90, "1": 10}),
            mitigator_pb2.ReqMitigationResponse(counts={"0": 40, "1": 60}),
        ]
    )

    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        preprocessed_qasms=["OPENQASM 3.0; include \"stdgates.inc\";"] * 2,
        counts_list=[{"0": 80, "1": 20}, {"0": 30, "1": 70}],
    )
    job = Job(
        job_id="job-ro-estimation",
        device_id="device-1",
        shots=1000,
        job_type="estimation",
        job_info=JobInfo(program=["OPENQASM 3.0; include \"stdgates.inc\";"]),
        transpiler_info={},
        simulator_info={},
        mitigation_info={"ro_error_mitigation": "pseudo_inverse"},
        status="ready",
    )

    await step.post_process(_build_gctx(), jctx, job)

    assert step._stub.ReqMitigation.await_count == 2
    assert jctx["estimation_job_info"].counts_list == [{"0": 90, "1": 10}, {"0": 40, "1": 60}]


@pytest.mark.asyncio
async def test_post_process_skips_without_readout_setting() -> None:
    step = ReadoutErrorMitigationStep()
    step._stub = AsyncMock()
    job = Job(
        job_id="job-no-ro",
        device_id="device-1",
        shots=100,
        job_type="sampling",
        job_info=JobInfo(
            program=["OPENQASM 3.0; include \"stdgates.inc\";"],
            result=JobResult(sampling=SamplingResult(counts={"0": 1})),
        ),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
    )

    await step.post_process(_build_gctx(), JobContext(), job)
    assert step._stub.ReqMitigation.await_count == 0


@pytest.mark.asyncio
async def test_post_process_estimation_applies_readout_mitigation_to_zne_execution_results() -> None:
    step = ReadoutErrorMitigationStep(mitigator_timeout_seconds=5)
    step._stub = AsyncMock()
    step._stub.ReqMitigation = AsyncMock(
        return_value=mitigator_pb2.ReqMitigationResponse(counts={"0": 95, "1": 5})
    )

    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        preprocessed_qasms=["OPENQASM 3.0; include \"stdgates.inc\";"],
        counts_list=None,
    )
    jctx["zne_job_info"] = {
        "execution_programs": [
            mitigator_pb2.ZneExecutionProgram(
                scale_factor=1.0,
                repetition=0,
                program_index=0,
                suffix="s1-r0-p0",
                program='OPENQASM 3.0; include "stdgates.inc";',
            )
        ],
        "execution_results": [
            {
                "scale_factor": 1.0,
                "repetition": 0,
                "program_index": 0,
                "counts": {"0": 90, "1": 10},
            }
        ],
    }
    job = Job(
        job_id="job-ro-zne-estimation",
        device_id="device-1",
        shots=1000,
        job_type="estimation",
        job_info=JobInfo(program=["OPENQASM 3.0; include \"stdgates.inc\";"]),
        transpiler_info={},
        simulator_info={},
        mitigation_info={"ro_error_mitigation": "pseudo_inverse", "zne": {"enabled": True}},
        status="ready",
    )

    await step.post_process(_build_gctx(), jctx, job)

    assert step._stub.ReqMitigation.await_count == 1
    assert jctx["zne_job_info"]["execution_results"][0]["counts"] == {"0": 95, "1": 5}
