import pytest

from oqtopus_engine_core.slurm import (
    OperatorTerm,
    QulacsExecutionRequest,
    read_worker_result,
)


def sampling_request() -> QulacsExecutionRequest:
    return QulacsExecutionRequest(
        job_type="sampling",
        n_qubits=2,
        gates=[],
        measurement_mapping={0: 1, 1: 0},
        shots=10,
        n_per_node=1,
    )


def test_read_sampling_result(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(
        '{"schema_version":1,"status":"succeeded","job_type":"sampling",'
        '"counts":{"00":4,"11":6},"duration_seconds":1.25}',
        encoding="utf-8",
    )

    result, duration = read_worker_result(
        result_path,
        sampling_request(),
        imaginary_tolerance=1e-10,
    )

    assert result.sampling is not None
    assert result.sampling.counts == {"00": 4, "11": 6}
    assert duration == 1.25


def test_read_sampling_result_rejects_count_mismatch(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(
        '{"schema_version":1,"status":"succeeded","job_type":"sampling",'
        '"counts":{"00":9},"duration_seconds":1.0}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sum to shots"):
        read_worker_result(
            result_path,
            sampling_request(),
            imaginary_tolerance=1e-10,
        )


def test_read_estimation_result_rejects_large_imaginary_component(tmp_path):
    request = QulacsExecutionRequest(
        job_type="estimation",
        n_qubits=1,
        gates=[],
        operators=[OperatorTerm(pauli="Z 0", coeff=1.0)],
        n_per_node=1,
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(
        '{"schema_version":1,"status":"succeeded","job_type":"estimation",'
        '"exp_value":[0.5,0.01],"duration_seconds":2.0}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="imaginary component"):
        read_worker_result(
            result_path,
            request,
            imaginary_tolerance=1e-6,
        )