from oqtopus_engine_core.framework.model import Job, resolve_repository_jobs


def test_job_repr_and_str_output():
    """Test that repr/str only show IDs for linked jobs and full repr for others."""

    # 1. Setup parent and multiple children
    parent_job = Job(
        job_id="parent_001",
        device_id="device_a",
        shots=1000,
        job_type="circuit",
        input="dummy_input",
        program=["dummy_program_content"],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="completed",
    )

    child_01 = Job(
        job_id="child_001",
        device_id="device_a",
        shots=1000,
        job_type="circuit",
        input="dummy_input",
        program=["dummy_program_content"],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
        parent=parent_job
    )

    child_02 = Job(
        job_id="child_002",
        device_id="device_a",
        shots=1000,
        job_type="circuit",
        input="dummy_input",
        program=["dummy_program_content"],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="pending",
        parent=parent_job
    )

    # Link multiple children to parent
    parent_job.children.extend([child_01, child_02])

    # 2. Verify Child's repr (Quotes removed, parent ID only)
    child_repr = repr(child_01)
    assert "job_id=child_001" in child_repr
    assert "parent=parent_001" in child_repr

    # 3. Verify Parent's repr with multiple children
    # It should show a list of child IDs: ['child_001', 'child_002']
    parent_repr = repr(parent_job)
    assert "job_id=parent_001" in parent_repr
    assert "children=['child_001', 'child_002']" in parent_repr

    # 4. Verify __str__ matches __repr__
    assert str(parent_job) == repr(parent_job)


def test_job_repr_with_none_values():
    """Test repr output when optional fields are None."""
    job = Job(
        job_id="job_empty",
        device_id="device_b",
        shots=1,
        job_type="test",
        input="dummy_input",
        program=["dummy_program_content"],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="pending",
    )

    res = repr(job)
    assert "name=None" in res
    assert "parent=None" not in res
    assert "children=[]" not in res


def _job(job_id: str, *, repository_job_id: str | None | object = ...) -> Job:
    """Build a minimal Job for resolve_repository_jobs tests.

    By default, `repository_job_id` equals `job_id` (the fetcher-origin
    rule). Pass an explicit value (including `None`) to override it.
    """
    job = Job(
        job_id=job_id,
        device_id="device_a",
        shots=1000,
        job_type="sampling",
        input="dummy_input",
        program=["dummy_program_content"],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
    )
    job.repository_job_id = job_id if repository_job_id is ... else repository_job_id
    return job


def test_resolve_repository_jobs_ordinary_sampling_resolves_to_itself():
    """1:1, an ordinary sampling/estimation parent resolves to itself."""
    job = _job("J")

    assert resolve_repository_jobs(job) == [job]


def test_resolve_repository_jobs_sampling_plus_mp_resolves_each_child():
    """1:0, a combined job with no Cloud entity delegates to its children.

    Each child (sampling + mp) still owns its own Cloud record, so each
    resolves to itself, not to the combined job.
    """
    child_1 = _job("J1")
    child_2 = _job("J2")
    combined = _job("mpa-comb-x", repository_job_id=None)
    combined.children = [child_1, child_2]
    child_1.parent = combined
    child_2.parent = combined

    resolved = resolve_repository_jobs(combined)

    assert resolved == [child_1, child_2]


def test_resolve_repository_jobs_estimation_child_resolves_to_parent():
    """N:1, an estimation child resolves upward to its parent."""
    parent = _job("P")
    child_1 = _job("P-estimation-0", repository_job_id="P")
    child_2 = _job("P-estimation-1", repository_job_id="P")
    parent.children = [child_1, child_2]
    child_1.parent = parent
    child_2.parent = parent

    resolved_1 = resolve_repository_jobs(child_1)
    resolved_2 = resolve_repository_jobs(child_2)

    assert resolved_1 == [parent]
    assert resolved_2 == [parent]
    # Both children resolve to the exact same object, so callers can dedupe
    # by job_id and still mutate the one shared Job.
    assert resolved_1[0] is resolved_2[0]


def test_resolve_repository_jobs_estimation_plus_mp_resolves_to_parent():
    """1:0 then N:1, a combined estimation-child job resolves to the parent.

    The child keeps its parent link to the estimation parent (not to the
    combined job) even after being re-emitted by MpAutoCombiningBuffer, so
    resolution still climbs to the estimation parent and not the combined
    job.
    """
    parent = _job("P")
    child = _job("P-estimation-0", repository_job_id="P")
    parent.children = [child]
    child.parent = parent
    combined = _job("mpa-comb-x", repository_job_id=None)
    combined.children = [child]  # child.parent stays `parent`, not `combined`

    assert resolve_repository_jobs(combined) == [parent]


def test_resolve_repository_jobs_dedupes_two_children_of_same_parent():
    """Two children of the same estimation parent, combined into the same
    combined job, resolve to that one parent exactly once, not twice.
    """
    parent = _job("P")
    child_1 = _job("P-estimation-0", repository_job_id="P")
    child_2 = _job("P-estimation-1", repository_job_id="P")
    parent.children = [child_1, child_2]
    child_1.parent = parent
    child_2.parent = parent
    combined = _job("mpa-comb-x", repository_job_id=None)
    combined.children = [child_1, child_2]

    assert resolve_repository_jobs(combined) == [parent]


def test_resolve_repository_jobs_combined_spans_multiple_parents():
    """1:N, a combined job whose children span two estimation parents."""
    parent_1 = _job("P1")
    parent_2 = _job("P2")
    child_1 = _job("P1-estimation-1", repository_job_id="P1")
    child_2 = _job("P2-estimation-0", repository_job_id="P2")
    child_1.parent = parent_1
    child_2.parent = parent_2
    combined = _job("mpa-comb-x", repository_job_id=None)
    combined.children = [child_1, child_2]

    assert resolve_repository_jobs(combined) == [parent_1, parent_2]


def test_resolve_repository_jobs_no_repository_entity_and_no_children_is_empty():
    """An internal job with nothing to delegate to resolves to an empty list.

    This is the SSE-internal-job shape: `repository_job_id` is left unset
    and there are no children to resolve through instead. Expected, not an
    error; callers must not treat an empty result as a failure.
    """
    job = _job("sse-internal", repository_job_id=None)

    assert resolve_repository_jobs(job) == []
