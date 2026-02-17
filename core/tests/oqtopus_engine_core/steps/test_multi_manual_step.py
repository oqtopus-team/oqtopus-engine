import pytest

from oqtopus_engine_core.framework import Job, JobResult
from oqtopus_engine_core.steps.multi_manual_step import (
    divide_result,
    divide_string_by_lengths,
)


@pytest.mark.parametrize(
    "input_str, lengths, expected, error",
    [
        ("", [], [], None),
        ("", [0, 0], ["", ""], None),
        ("01", [2], ["01"], None),
        ("011000101", [2, 3, 4], ["01", "100", "0101"], None),
        ("011000101", [2, 3, 0, 4, 0], ["01", "100", "", "0101", ""], None),
        ("0110001011", [2, 3, 4], None, "inconsistent qubits"),
        ("01100010", [2, 3, 4], None, "inconsistent qubits"),
        ("011000101", [2, 3, 4, 1], None, "inconsistent qubits"),
        ("011000101", [2, 3], None, "inconsistent qubits"),
        ("011000101", [], None, "inconsistent qubits"),
        ("", [1, 2], None, "inconsistent qubits"),
    ],
)
def test_divide_string_by_lengths(input_str, lengths, expected, error):
    if error:
        with pytest.raises(ValueError) as e:
            divide_string_by_lengths(input_str, lengths)
        assert error in str(e.value)
    else:
        assert divide_string_by_lengths(input_str, lengths) == expected


@pytest.mark.parametrize(
    "counts, combined_qubits_list, expected_counts, expected_divided, error",
    [
        # 1 circuit
        (
            {
                "0001": 1,
                "0100": 2,
                "1000": 4,
                "1111": 8,
                "0010": 16,
                "0110": 32,
                "1011": 64,
            },
            [4],
            {
                "0001": 1,
                "0100": 2,
                "1000": 4,
                "1111": 8,
                "0010": 16,
                "0110": 32,
                "1011": 64,
            },
            {
                0: {
                    "0001": 1,
                    "0100": 2,
                    "1000": 4,
                    "0010": 16,
                    "0110": 32,
                    "1011": 64,
                    "1111": 8,
                }
            },
            None,
        ),
        # 2 circuits
        (
            {
                "0001": 1,
                "0100": 2,
                "1000": 4,
                "1111": 8,
                "0010": 16,
                "0110": 32,
                "1011": 64,
            },
            [3, 1],
            {
                "0001": 1,
                "0100": 2,
                "1000": 4,
                "1111": 8,
                "0010": 16,
                "0110": 32,
                "1011": 64,
            },
            {
                0: {"0": 54, "1": 73},
                1: {
                    "000": 1,
                    "010": 2,
                    "100": 4,
                    "001": 16,
                    "011": 32,
                    "101": 64,
                    "111": 8,
                },
            },
            None,
        ),
        # Negative test - no qubit
        ({}, [], {}, None, "inconsistent qubit property"),
    ],
)
def test_divide_result(counts, combined_qubits_list, expected_counts, expected_divided, error):
    job_result = JobResult(
        sampling={
            "counts": counts,
            "divided_counts": None,
        },
        estimation=None,
    )
    jd = Job(
        device_id="test_device",
        job_type="test_type",
        shots=1024,
        name="test_job",
        description="test_description",
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="COMPLETED",
        job_id="test_job",
        job_info={
            "program": ["test_program"],
            "combined_program": "test_combined_program",
            "transpile_result": None,
            "message": None,
            "result": job_result,
            "operator": [],
        },
    )
    jctx = {"combined_qubits_list": combined_qubits_list}
    if error:
        with pytest.raises(ValueError) as e:
            divide_result(job=jd, jctx=jctx)
        assert error in str(e.value)
    else:
        divided_counts = divide_result(job=jd, jctx=jctx)
        assert jd.job_info.result.sampling.counts == expected_counts
        assert divided_counts == expected_divided
