from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.framework.pipeline_condition import compile_condition
from oqtopus_engine_core.framework.pipeline_manager import PipelineSelector

# ---------------------------------------------------------------------------
# Helper factory functions
# ---------------------------------------------------------------------------

def make_test_job(job_id: str, job_type: str = "root") -> Job:
    """Create a minimal but valid Job instance for selector tests."""
    return Job(
        job_id=job_id,
        job_type=job_type,
        device_id="test-device",
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

def test_selector_returns_first_matching_pipeline_name():
    """
    When multiple pipelines could match the same job, the selector must
    pick the first one in evaluation order (design doc 3章).
    """
    conditions = [
        ("first", compile_condition('job.job_type == "sampling"')),
        ("second", compile_condition('job.job_type == "sampling"')),
    ]
    selector = PipelineSelector(conditions)

    assert selector.select(make_test_job("j1", job_type="sampling")) == "first"


def test_selector_returns_none_when_no_pipeline_matches():
    conditions = [("sampling", compile_condition('job.job_type == "sampling"'))]
    selector = PipelineSelector(conditions)

    assert selector.select(make_test_job("j1", job_type="unknown_type")) is None


def test_selector_routes_by_job_type():
    conditions = [
        ("sampling", compile_condition('job.job_type == "sampling"')),
        ("sse", compile_condition('job.job_type == "sse"')),
        ("catch_all", compile_condition("true")),
    ]
    selector = PipelineSelector(conditions)

    assert selector.select(make_test_job("j1", job_type="sse")) == "sse"
    assert selector.select(make_test_job("j2", job_type="estimation")) == "catch_all"
