import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.steps.ro_error_mitigation_step import ReadoutErrorMitigationStep


@pytest.fixture
def setup_sampling_job():
    gctx = MagicMock()
    gctx.device.device_info = json.dumps(
        {
            "qubits": [
                {"meas_error": {"prob_meas1_prep0": 0.01, "prob_meas0_prep1": 0.02}},
                {"meas_error": {"prob_meas1_prep0": 0.03, "prob_meas0_prep1": 0.04}},
            ]
        }
    )

    jctx: dict[str, object] = {}

    job = MagicMock()
    job.job_id = "job-1"
    job.job_type = "sampling"
    job.mitigation_info = {"ro_error_mitigation": "pseudo_inverse"}
    job.job_info.program = ["OPENQASM 3.0;\n"]
    job.job_info.result.sampling.counts = {"00": 500, "01": 300, "10": 150, "11": 50}

    return gctx, jctx, job


@pytest.fixture
def mitigation_step() -> ReadoutErrorMitigationStep:
    step = ReadoutErrorMitigationStep("localhost:52011")
    step._stub = MagicMock()
    step._stub.ReqMitigation = AsyncMock()
    return step


@pytest.mark.asyncio
async def test_post_process_sampling_calls_grpc_and_updates_counts(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, jctx, job = setup_sampling_job
    mitigation_step._stub.ReqMitigation.return_value = SimpleNamespace(
        counts={"00": 480, "01": 320, "10": 140, "11": 60}
    )

    await mitigation_step.post_process(gctx, jctx, job)

    mitigation_step._stub.ReqMitigation.assert_awaited_once()
    request = mitigation_step._stub.ReqMitigation.call_args.args[0]

    assert dict(request.counts) == {"00": 500, "01": 300, "10": 150, "11": 50}
    assert request.program == "OPENQASM 3.0;\n"
    assert len(request.device_topology.qubits) == 2
    assert request.device_topology.qubits[0].mes_error.p0m1 == pytest.approx(0.01)
    assert request.device_topology.qubits[0].mes_error.p1m0 == pytest.approx(0.02)
    assert request.device_topology.qubits[1].mes_error.p0m1 == pytest.approx(0.03)
    assert request.device_topology.qubits[1].mes_error.p1m0 == pytest.approx(0.04)

    assert job.job_info.result.sampling.counts == {
        "00": 480,
        "01": 320,
        "10": 140,
        "11": 60,
    }


@pytest.mark.asyncio
async def test_post_process_skips_when_mitigation_is_unset(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, jctx, job = setup_sampling_job
    original_counts = dict(job.job_info.result.sampling.counts)

    job.mitigation_info = {}
    await mitigation_step.post_process(gctx, jctx, job)

    job.mitigation_info = {"ro_error_mitigation": None}
    await mitigation_step.post_process(gctx, jctx, job)

    mitigation_step._stub.ReqMitigation.assert_not_awaited()
    assert job.job_info.result.sampling.counts == original_counts


@pytest.mark.asyncio
async def test_post_process_non_sampling_job_is_skipped(
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx = MagicMock()
    gctx.device.device_info = json.dumps({"qubits": []})

    job = MagicMock()
    job.job_id = "job-2"
    job.job_type = "estimation"
    job.mitigation_info = {"ro_error_mitigation": "pseudo_inverse"}
    job.job_info.program = ["ignored-program"]

    await mitigation_step.post_process(gctx, {}, job)

    mitigation_step._stub.ReqMitigation.assert_not_awaited()
