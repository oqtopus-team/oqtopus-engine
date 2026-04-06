import pytest

from oqtopus_engine_core.framework.context import JobContext, PipelineDirective, link_parent_and_children
from oqtopus_engine_core.framework.model import Job, JobInfo


def _make_minimal_job(job_id: str) -> Job:
    """Create a minimal Job object for testing."""
    return Job(
        job_id=job_id,
        job_type="sampling",
        device_id="test-device",
        shots=100,
        job_info=JobInfo(program=[]),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
    )


def test_link_parent_and_children_success():
    """
    Test that bidirectional links are correctly established between
    parent and multiple children.
    """
    # 1. Setup parent and children
    parent_job = _make_minimal_job("parent")
    parent_jctx = JobContext({"name": "parent_ctx"})

    child_jobs = [_make_minimal_job("child-0"), _make_minimal_job("child-1")]
    child_jctxs = [JobContext({"id": 0}), JobContext({"id": 1})]

    # 2. Execute linkage
    link_parent_and_children(parent_job, parent_jctx, child_jobs, child_jctxs)

    # 3. Verify Parent -> Children links
    assert parent_job.children == child_jobs
    assert parent_jctx.children == child_jctxs

    # 4. Verify Children -> Parent links
    for i in range(2):
        # Check Job link
        assert child_jobs[i].parent == parent_job
        # Check JobContext link
        assert child_jctxs[i].parent == parent_jctx
        # Check context flag used for traversal logic


def test_link_parent_and_children_length_mismatch():
    """
    Test that ValueError is raised when the number of jobs and contexts
    do not match.
    """
    parent_job = _make_minimal_job("parent")
    parent_jctx = JobContext()

    # 1 job vs 2 contexts
    child_jobs = [_make_minimal_job("child-0")]
    child_jctxs = [JobContext(), JobContext()]

    with pytest.raises(ValueError, match="The number of child jobs and child contexts must match"):
        link_parent_and_children(parent_job, parent_jctx, child_jobs, child_jctxs)


def test_job_context_reserved_attributes_isolation():
    """
    Test that 'parent' attribute in JobContext does not leak into
    the internal data dictionary.
    """
    parent_jctx = JobContext()
    child_jctx = JobContext({"user_data": "value"})

    # Set parent via utility or directly
    child_jctx.parent = parent_jctx

    # Should be accessible as an attribute
    assert child_jctx.parent == parent_jctx
    # Should NOT be present in the underlying dict data (important for serialization)
    assert "parent" not in child_jctx.data
    # Normal data should remain intact
    assert child_jctx["user_data"] == "value"


def test_pipeline_directive_default_is_none():
    """Test that pipeline_directive is initialized to PipelineDirective.NONE."""
    jctx = JobContext()
    assert jctx.pipeline_directive is PipelineDirective.NONE


def test_pipeline_directive_can_be_set():
    """Test that pipeline_directive can be set to a non-NONE value."""
    jctx = JobContext()
    jctx.pipeline_directive = PipelineDirective.IGNORE_SPLIT_TRACKING
    assert jctx.pipeline_directive is PipelineDirective.IGNORE_SPLIT_TRACKING


def test_pipeline_directive_reset_to_none():
    """Test that pipeline_directive can be reset back to NONE."""
    jctx = JobContext()
    jctx.pipeline_directive = PipelineDirective.IGNORE_SPLIT_TRACKING
    jctx.pipeline_directive = PipelineDirective.NONE
    assert jctx.pipeline_directive is PipelineDirective.NONE


def test_pipeline_directive_not_in_data_dict():
    """Test that pipeline_directive does not leak into the internal data dictionary."""
    jctx = JobContext()
    jctx.pipeline_directive = PipelineDirective.IGNORE_SPLIT_TRACKING
    assert "pipeline_directive" not in jctx.data


def test_pipeline_directive_cannot_be_deleted():
    """Test that pipeline_directive raises AttributeError on deletion attempt."""
    jctx = JobContext()
    with pytest.raises(AttributeError, match="reserved attribute"):
        del jctx.pipeline_directive

