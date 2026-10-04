import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.framework.context import GlobalContext, JobContext
from oqtopus_engine_core.framework.model import Device, Job, TranspileResult
from oqtopus_engine_core.mp.auto_combining.mp_auto_combining_buffer import (
    MpAutoCombiningBuffer,
    _group_by_pipeline_name,  # noqa: PLC2701
    create_combined_job,
)

SAMPLE_PROGRAM = """OPENQASM 3;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c = measure q;
"""


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
    return GlobalContext(
        config={},
        device=Device(
            device_id="test-device",
            device_type="simulator",
            status="available",
            n_qubits=4,
            basis_gates=[],
            instructions=[],
            description="",
        ),
    )


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
    # The combined job has no Cloud entity of its own; repository updates
    # must resolve to its children instead (see resolve_repository_jobs).
    assert combined_job.repository_job_id is None


def _combine_response(job_id: str) -> SimpleNamespace:
    """Build a fake OptimalCombine gRPC response combining a single job."""
    combine_result = {
        "combined_groups": [
            {
                "combine_info": {
                    "assigned_ids": [job_id],
                    "assigned_group": [
                        {"job_id": job_id, "qubit_mapping": {"0": 2, "1": 3}}
                    ],
                    "n_total_qubits": 4,
                    "combined_qubits_list": [2, 2],
                },
                "combined_program": "OPENQASM 3;",
            }
        ],
        "assigned_ids": [job_id],
    }
    return SimpleNamespace(combine_result=json.dumps(combine_result))


def _make_combinable_job(job_id: str, repository_job_id: str) -> Job:
    return Job(
        job_id=job_id,
        repository_job_id=repository_job_id,
        job_type="sampling",
        device_id="test-device",
        shots=100,
        input="test-input",
        program=[],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
        transpile_result=TranspileResult(
            transpiled_program=SAMPLE_PROGRAM,
            stats={},
            virtual_physical_mapping={"qubit_mapping": {"0": 0, "1": 1}},
        ),
    )


@pytest.mark.asyncio
async def test_combine_jobs_uploads_transpile_result_when_job_owns_its_own_record() -> (
    None
):
    """sampling + mp: the original job's repository_job_id == job_id (it
    owns its own Cloud record), so the remapped transpile_result is still
    uploaded under its own ID after combining. This is the regression case
    for the _upload_transpile_result guard: it must not break the existing
    sampling + mp upload behavior.
    """
    buffer = MpAutoCombiningBuffer(monitor_interval_seconds=0.01)
    try:
        buffer._stub.OptimalCombine = AsyncMock(return_value=_combine_response("a"))  # noqa: SLF001
        gctx = make_test_global_context()
        gctx.job_repository = MagicMock()
        gctx.job_repository.upload_job_outputs_nowait = AsyncMock()
        job = _make_combinable_job("a", repository_job_id="a")

        await buffer._combine_jobs([(gctx, JobContext(), job)])  # noqa: SLF001

        gctx.job_repository.upload_job_outputs_nowait.assert_awaited_once()
        assert (
            gctx.job_repository.upload_job_outputs_nowait.await_args.kwargs["job"]
            is job
        )
    finally:
        await buffer.stop()


@pytest.mark.asyncio
async def test_combine_jobs_skips_upload_when_job_is_estimation_child() -> None:
    """estimation + mp: the child's repository_job_id names its parent, not
    itself, so the remapped transpile_result must NOT be uploaded under the
    child's internal job_id; only the parent (pre-combining) transpile_result
    is reported (see docs/features/estimation/overview.md). The in-memory
    remap of the child's own transpile_result still happens; only the
    upload is skipped.
    """
    buffer = MpAutoCombiningBuffer(monitor_interval_seconds=0.01)
    try:
        buffer._stub.OptimalCombine = AsyncMock(  # noqa: SLF001
            return_value=_combine_response("P-estimation-0")
        )
        gctx = make_test_global_context()
        gctx.job_repository = MagicMock()
        gctx.job_repository.upload_job_outputs_nowait = AsyncMock()
        job = _make_combinable_job("P-estimation-0", repository_job_id="P")

        await buffer._combine_jobs([(gctx, JobContext(), job)])  # noqa: SLF001

        gctx.job_repository.upload_job_outputs_nowait.assert_not_awaited()
        assert job.transpile_result.virtual_physical_mapping["qubit_mapping"] == {
            "0": 2,
            "1": 3,
        }
    finally:
        await buffer.stop()
