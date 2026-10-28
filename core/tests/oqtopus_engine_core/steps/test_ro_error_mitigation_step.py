import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.interfaces.mitigator_interface.v1 import mitigator_pb2
from oqtopus_engine_core.steps.estimator_step import (
    ESTIMATION_EXPECTATION_VALUES_KEY,
    ESTIMATION_PAULIS_KEY,
    ESTIMATION_STANDARD_DEVIATION_UPPER_BOUNDS_KEY,
    ESTIMATION_BEFORE_EXPECTATION_VALUES_KEY,
)
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
    job.program = ["OPENQASM 3.0;\n"]
    job.result.sampling.counts = {"00": 500, "01": 300, "10": 150, "11": 50}

    return gctx, jctx, job


@pytest.fixture
def mitigation_step() -> ReadoutErrorMitigationStep:
    step = ReadoutErrorMitigationStep("localhost:52011")
    step._stub = MagicMock()
    step._stub.ReqMitigation = AsyncMock()
    step._stub.ReqExpectationValueMitigation = AsyncMock()
    return step


@pytest.mark.asyncio
async def test_post_process_sampling_calls_grpc_and_updates_counts(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, jctx, job = setup_sampling_job
    mitigation_step._stub.ReqMitigation.return_value = SimpleNamespace(
        counts={"00": 480, "01": 320, "10": 140, "11": 60},
        quasi_probabilities={
            "00": 0.48,
            "01": -0.02,
            "10": 0.14,
            "11": 0.4,
        },
        mitigation_details_available=True,
    )

    await mitigation_step.post_process(gctx, jctx, job)

    mitigation_step._stub.ReqMitigation.assert_awaited_once()
    mitigation_step._stub.ReqExpectationValueMitigation.assert_not_awaited()
    request = mitigation_step._stub.ReqMitigation.call_args.args[0]

    assert dict(request.counts) == {"00": 500, "01": 300, "10": 150, "11": 50}
    assert request.program == "OPENQASM 3.0;\n"
    assert len(request.device_topology.qubits) == 2
    assert request.device_topology.qubits[0].mes_error.p0m1 == pytest.approx(0.01)
    assert request.device_topology.qubits[0].mes_error.p1m0 == pytest.approx(0.02)
    assert request.device_topology.qubits[1].mes_error.p0m1 == pytest.approx(0.03)
    assert request.device_topology.qubits[1].mes_error.p1m0 == pytest.approx(0.04)

    assert job.result.sampling.counts == {
        "00": 480,
        "01": 320,
        "10": 140,
        "11": 60,
    }
    details = job.result.mitigation_details.ro_error_mitigation
    assert details.method == "local_readout_mitigation"
    assert details.raw_counts == {
        "00": 500,
        "01": 300,
        "10": 150,
        "11": 50,
    }
    assert details.quasi_probabilities == {
        "00": 0.48,
        "01": -0.02,
        "10": 0.14,
        "11": 0.4,
    }
    assert details.expectation_values is None


@pytest.mark.asyncio
async def test_post_process_skips_when_mitigation_is_unset(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, jctx, job = setup_sampling_job
    original_counts = dict(job.result.sampling.counts)

    job.mitigation_info = {}
    await mitigation_step.post_process(gctx, jctx, job)

    job.mitigation_info = {"ro_error_mitigation": None}
    await mitigation_step.post_process(gctx, jctx, job)

    mitigation_step._stub.ReqMitigation.assert_not_awaited()
    assert job.result.sampling.counts == original_counts


@pytest.mark.asyncio
async def test_post_process_estimation_child_updates_expectation_values(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, _, job = setup_sampling_job
    jctx = {ESTIMATION_PAULIS_KEY: ["XX", "II"]}
    original_counts = dict(job.result.sampling.counts)
    job.result.mitigation_details = None
    mitigation_step._stub.ReqExpectationValueMitigation.return_value = SimpleNamespace(
        expectation_values=[0.8, 1.0],
        standard_deviation_upper_bounds=[0.03, 0.0],
        before_expectation_values=[0.6, 1.0],
        mitigation_details_available=True,
    )

    await mitigation_step.post_process(gctx, jctx, job)

    mitigation_step._stub.ReqMitigation.assert_not_awaited()
    mitigation_step._stub.ReqExpectationValueMitigation.assert_awaited_once()
    request = mitigation_step._stub.ReqExpectationValueMitigation.call_args.args[0]
    assert list(request.paulis) == ["XX", "II"]
    assert job.result.sampling.counts == original_counts
    assert jctx[ESTIMATION_EXPECTATION_VALUES_KEY] == [0.8, 1.0]
    assert jctx[ESTIMATION_STANDARD_DEVIATION_UPPER_BOUNDS_KEY] == [0.03, 0.0]
    assert jctx[ESTIMATION_BEFORE_EXPECTATION_VALUES_KEY] == [0.6, 1.0]
    assert job.result.mitigation_details is None


@pytest.mark.asyncio
async def test_post_process_estimation_child_rejects_mismatched_response(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, _, job = setup_sampling_job
    jctx = {ESTIMATION_PAULIS_KEY: ["XX"]}
    mitigation_step._stub.ReqExpectationValueMitigation.return_value = SimpleNamespace(
        expectation_values=[0.8],
        standard_deviation_upper_bounds=[],
        before_expectation_values=[0.6],
        mitigation_details_available=True,
    )

    with pytest.raises(RuntimeError, match="must have equal lengths"):
        await mitigation_step.post_process(gctx, jctx, job)


@pytest.mark.asyncio
async def test_post_process_sampling_accepts_legacy_response_without_details(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, jctx, job = setup_sampling_job
    job.result.mitigation_details = None
    mitigation_step._stub.ReqMitigation.return_value = (
        mitigator_pb2.ReqMitigationResponse(counts={"00": 60, "11": 40})
    )

    await mitigation_step.post_process(gctx, jctx, job)

    assert job.result.sampling.counts == {"00": 60, "11": 40}
    assert job.result.mitigation_details is None


@pytest.mark.asyncio
async def test_post_process_estimation_accepts_legacy_response_without_details(
    setup_sampling_job,
    mitigation_step: ReadoutErrorMitigationStep,
) -> None:
    gctx, _, job = setup_sampling_job
    jctx = {ESTIMATION_PAULIS_KEY: ["XX"]}
    mitigation_step._stub.ReqExpectationValueMitigation.return_value = (
        mitigator_pb2.ReqExpectationValueMitigationResponse(
            expectation_values=[0.8],
            standard_deviation_upper_bounds=[0.03],
        )
    )

    await mitigation_step.post_process(gctx, jctx, job)

    assert jctx[ESTIMATION_EXPECTATION_VALUES_KEY] == [0.8]
    assert jctx[ESTIMATION_STANDARD_DEVIATION_UPPER_BOUNDS_KEY] == [0.03]
    assert ESTIMATION_BEFORE_EXPECTATION_VALUES_KEY not in jctx


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
    job.program = ["ignored-program"]

    await mitigation_step.post_process(gctx, {}, job)

    mitigation_step._stub.ReqMitigation.assert_not_awaited()
    mitigation_step._stub.ReqExpectationValueMitigation.assert_not_awaited()
