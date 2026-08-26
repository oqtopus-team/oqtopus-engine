from oqtopus_engine_core.framework.context import GlobalContext, JobContext
from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.mp.auto_combining.mp_auto_combining_buffer import (
    _group_by_pipeline_name,  # noqa: PLC2701
    create_combined_job,
)


def make_test_job(job_id: str, job_type: str = "sampling") -> Job:
    """Create a minimal but valid Job instance for combining-buffer tests."""
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


def make_test_global_context() -> GlobalContext:
    """Create a minimal GlobalContext instance for combining-buffer tests."""
    return GlobalContext(config={})


def test_group_by_pipeline_name_groups_and_preserves_order():
    """
    Jobs are grouped by their jctx's pipeline_name, preserving relative order
    within each group, so combining never mixes jobs across pipelines.
    """
    gctx = make_test_global_context()
    job_a = make_test_job("a")
    job_b = make_test_job("b")
    job_c = make_test_job("c")
    jctx_a = JobContext()
    jctx_a["pipeline_name"] = "estimation"
    jctx_b = JobContext()
    jctx_b["pipeline_name"] = "sampling"
    jctx_c = JobContext()
    jctx_c["pipeline_name"] = "estimation"

    groups = _group_by_pipeline_name([
        (gctx, jctx_a, job_a),
        (gctx, jctx_b, job_b),
        (gctx, jctx_c, job_c),
    ])

    assert set(groups.keys()) == {"estimation", "sampling"}
    assert [item[2].job_id for item in groups["estimation"]] == ["a", "c"]
    assert [item[2].job_id for item in groups["sampling"]] == ["b"]


def test_group_by_pipeline_name_missing_pipeline_name_groups_under_none():
    """Jobs with no pipeline_name set are grouped together under the None key."""
    gctx = make_test_global_context()
    job = make_test_job("solo")
    jctx = JobContext()

    groups = _group_by_pipeline_name([(gctx, jctx, job)])

    assert list(groups.keys()) == [None]


def test_create_combined_job_inherits_shared_pipeline_name():
    """
    The combined job's pipeline_name is taken directly from its constituent
    jobs (all sharing one pipeline_name, since combining is grouped by
    pipeline_name before this is called) rather than being re-derived from
    job_type, which would be wrong for e.g. estimation sub-circuits whose
    job_type ("sampling") does not match their real pipeline_name.
    """
    gctx = make_test_global_context()
    job_a = make_test_job("a")
    job_b = make_test_job("b")
    jctx_a = JobContext()
    jctx_a["pipeline_name"] = "estimation"
    jctx_b = JobContext()
    jctx_b["pipeline_name"] = "estimation"

    original_jobs = {
        "a": (gctx, jctx_a, job_a),
        "b": (gctx, jctx_b, job_b),
    }
    combine_info = {"n_total_qubits": 4, "combined_qubits_list": [2, 2]}

    _, combined_jctx, combined_job = create_combined_job(
        combined_program="OPENQASM 3;",
        combine_info=combine_info,
        original_jobs=original_jobs,
    )

    assert combined_jctx.get("pipeline_name") == "estimation"
    assert combined_job.children == [job_a, job_b]
