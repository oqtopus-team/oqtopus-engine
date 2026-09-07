import pytest

from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.framework.pipeline_condition import (
    ConditionFieldError,
    ConditionSyntaxError,
    ConditionTypeError,
    compile_condition,
    evaluate,
)

# ---------------------------------------------------------------------------
# Helper factory functions
# ---------------------------------------------------------------------------

def make_test_job(job_id: str, job_type: str = "root", device_id: str = "device-a") -> Job:
    """Create a minimal but valid Job instance for condition tests."""
    return Job(
        job_id=job_id,
        job_type=job_type,
        device_id=device_id,
        shots=100,
        input="test-input",
        program=[],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="CREATED",
    )


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_parse_and_evaluate_simple_equality():
    ast = compile_condition('job.job_type == "sampling"')
    assert evaluate(ast, make_test_job("j1", job_type="sampling")) is True
    assert evaluate(ast, make_test_job("j1", job_type="estimation")) is False


def test_parse_and_evaluate_not_equal():
    ast = compile_condition('job.job_type != "sse"')
    assert evaluate(ast, make_test_job("j1", job_type="sampling")) is True
    assert evaluate(ast, make_test_job("j1", job_type="sse")) is False


def test_and_or_not_precedence():
    """`a || b && c` must parse as `a || (b && c)`, not `(a || b) && c`."""
    ast = compile_condition(
        'job.job_type == "a" || job.job_type == "b" && job.device_id == "x"'
    )
    # job_type == "b" alone (device_id != "x") must NOT satisfy `b && c`,
    # so the whole expression must be False under (a || b) && c semantics
    # but True under a || (b && c) semantics only when device_id == "x".
    job_b_wrong_device = make_test_job("j1", job_type="b", device_id="y")
    assert evaluate(ast, job_b_wrong_device) is False

    job_b_right_device = make_test_job("j2", job_type="b", device_id="x")
    assert evaluate(ast, job_b_right_device) is True

    job_a = make_test_job("j3", job_type="a", device_id="y")
    assert evaluate(ast, job_a) is True


def test_not_negates_whole_comparison():
    ast = compile_condition('!(job.job_type == "sse")')
    assert evaluate(ast, make_test_job("j1", job_type="sse")) is False
    assert evaluate(ast, make_test_job("j1", job_type="sampling")) is True


def test_catch_all_true_literal():
    ast = compile_condition("true")
    assert evaluate(ast, make_test_job("j1", job_type="anything")) is True


def test_syntax_error_raises_condition_syntax_error():
    with pytest.raises(ConditionSyntaxError):
        compile_condition("job.job_type ==")


def test_unknown_field_raises_condition_field_error():
    with pytest.raises(ConditionFieldError):
        compile_condition('job.unknown_field == "x"')


def test_type_mismatch_raises_condition_type_error():
    with pytest.raises(ConditionTypeError):
        compile_condition("job.job_type == true")


def test_bare_nonbool_field_raises_condition_type_error():
    with pytest.raises(ConditionTypeError):
        compile_condition("job.job_type")


def test_and_operand_type_mismatch_raises_condition_type_error():
    """Non-bool field used as an && operand, not just at the top level."""
    with pytest.raises(ConditionTypeError):
        compile_condition('job.job_type && job.job_type == "sampling"')
