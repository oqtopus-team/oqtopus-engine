import pytest

from oqtopus_engine_core.framework.step import PipelineDirective, StepResult
from oqtopus_engine_core.framework.model import Job


def _make_job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        job_type="sampling",
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
# StepResult construction — valid cases
# ---------------------------------------------------------------------------


def test_step_result_default_is_none():
    """Default StepResult has NONE directive and no children."""
    result = StepResult()
    assert result.directive == PipelineDirective.NONE
    assert result.child_jobs is None
    assert result.child_contexts is None


def test_step_result_join_no_children():
    """JOIN directive requires no child_jobs or child_contexts."""
    result = StepResult(directive=PipelineDirective.JOIN)
    assert result.directive == PipelineDirective.JOIN


def test_step_result_detach_no_children():
    """DETACH directive requires no child_jobs or child_contexts."""
    result = StepResult(directive=PipelineDirective.DETACH)
    assert result.directive == PipelineDirective.DETACH


def test_step_result_split_for_join_with_children():
    """SPLIT_FOR_JOIN with matching child_jobs and child_contexts is valid."""
    jobs = [_make_job("child-0"), _make_job("child-1")]
    from oqtopus_engine_core.framework.context import JobContext
    ctxs = [JobContext(), JobContext()]
    result = StepResult(
        directive=PipelineDirective.SPLIT_FOR_JOIN,
        child_jobs=jobs,
        child_contexts=ctxs,
    )
    assert result.directive == PipelineDirective.SPLIT_FOR_JOIN
    assert len(result.child_jobs) == 2  # type: ignore[arg-type]
    assert len(result.child_contexts) == 2  # type: ignore[arg-type]


def test_step_result_split_without_join_with_children():
    """SPLIT_WITHOUT_JOIN with matching child_jobs and child_contexts is valid."""
    jobs = [_make_job("child-0")]
    from oqtopus_engine_core.framework.context import JobContext
    ctxs = [JobContext()]
    result = StepResult(
        directive=PipelineDirective.SPLIT_WITHOUT_JOIN,
        child_jobs=jobs,
        child_contexts=ctxs,
    )
    assert result.directive == PipelineDirective.SPLIT_WITHOUT_JOIN


# ---------------------------------------------------------------------------
# StepResult construction — invalid cases
# ---------------------------------------------------------------------------


def test_step_result_split_for_join_missing_child_jobs():
    """SPLIT_FOR_JOIN without child_jobs raises ValueError."""
    from oqtopus_engine_core.framework.context import JobContext
    with pytest.raises(ValueError, match="requires child_jobs"):
        StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_contexts=[JobContext()],
        )


def test_step_result_split_for_join_missing_child_contexts():
    """SPLIT_FOR_JOIN without child_contexts raises ValueError."""
    with pytest.raises(ValueError, match="requires child_contexts"):
        StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_jobs=[_make_job("child-0")],
        )


def test_step_result_split_for_join_length_mismatch():
    """SPLIT_FOR_JOIN with mismatched list lengths raises ValueError."""
    from oqtopus_engine_core.framework.context import JobContext
    with pytest.raises(ValueError, match="same length"):
        StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_jobs=[_make_job("child-0"), _make_job("child-1")],
            child_contexts=[JobContext()],
        )


def test_step_result_split_without_join_missing_child_jobs():
    """SPLIT_WITHOUT_JOIN without child_jobs raises ValueError."""
    from oqtopus_engine_core.framework.context import JobContext
    with pytest.raises(ValueError, match="requires child_jobs"):
        StepResult(
            directive=PipelineDirective.SPLIT_WITHOUT_JOIN,
            child_contexts=[JobContext()],
        )


def test_step_result_none_with_child_jobs_raises():
    """NONE directive must not include child_jobs."""
    with pytest.raises(ValueError, match="must not include"):
        StepResult(
            directive=PipelineDirective.NONE,
            child_jobs=[_make_job("child-0")],
        )


def test_step_result_join_with_child_jobs_raises():
    """JOIN directive must not include child_jobs."""
    with pytest.raises(ValueError, match="must not include"):
        StepResult(
            directive=PipelineDirective.JOIN,
            child_jobs=[_make_job("child-0")],
        )


def test_step_result_detach_with_child_contexts_raises():
    """DETACH directive must not include child_contexts."""
    from oqtopus_engine_core.framework.context import JobContext
    with pytest.raises(ValueError, match="must not include"):
        StepResult(
            directive=PipelineDirective.DETACH,
            child_contexts=[JobContext()],
        )
