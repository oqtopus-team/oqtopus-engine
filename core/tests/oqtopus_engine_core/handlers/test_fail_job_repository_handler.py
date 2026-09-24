from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.framework import JobContext
from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.handlers.fail_job_repository_handler import (
    FailJobRepositoryHandler,
)


def _job(job_id: str, *, repository_job_id: str | None = None) -> Job:
    job = Job(
        job_id=job_id,
        repository_job_id=repository_job_id if repository_job_id else job_id,
        job_type="sampling",
        device_id="test-device",
        shots=1,
        input="test-input",
        program=[],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
    )
    return job


@pytest.mark.asyncio
async def test_handle_exception_ordinary_job_fails_itself_with_original_message() -> (
    None
):
    """1:1 — an ordinary job fails itself with the original exception message."""
    handler = FailJobRepositoryHandler()
    gctx = MagicMock()
    gctx.job_repository.update_job_status = AsyncMock()
    job = _job("J")

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    assert job.status == "failed"
    assert job.message == "boom"
    gctx.job_repository.update_job_status.assert_awaited_once_with(job=job)


@pytest.mark.asyncio
async def test_handle_exception_estimation_child_fails_parent_only() -> None:
    """N:1 — an exception on an estimation child fails the parent, not the
    child (the child has no Cloud record of its own).
    """
    handler = FailJobRepositoryHandler()
    gctx = MagicMock()
    gctx.job_repository.update_job_status = AsyncMock()

    parent = _job("P")
    child = _job("P-estimation-0", repository_job_id="P")
    child.parent = parent
    parent.children = [child]

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), child)

    assert parent.status == "failed"
    assert parent.message == "boom"
    gctx.job_repository.update_job_status.assert_awaited_once_with(job=parent)


@pytest.mark.asyncio
async def test_handle_exception_combined_job_fails_all_children_with_combined_message() -> (
    None
):
    """1:0 — an exception on a combined job (sampling + mp) fails every
    child with a generic "combined execution failed" message rather than
    leaking the raw exception (or another job's job_id) across unrelated
    original jobs.
    """
    handler = FailJobRepositoryHandler()
    gctx = MagicMock()
    gctx.job_repository.update_job_status = AsyncMock()

    child_1 = _job("J1")
    child_2 = _job("J2")
    combined = _job("mpa-comb-x", repository_job_id=None)
    combined.repository_job_id = None
    combined.children = [child_1, child_2]
    child_1.parent = combined
    child_2.parent = combined

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), combined)

    assert child_1.status == "failed"
    assert child_2.status == "failed"
    assert child_1.message == "combined execution failed (multi-programming): boom"
    assert child_2.message == "combined execution failed (multi-programming): boom"
    assert gctx.job_repository.update_job_status.await_count == 2


@pytest.mark.asyncio
async def test_handle_exception_empty_resolution_updates_nothing() -> None:
    """An internal job with no repository entity and no children to
    delegate to (SSE-internal shape) is a no-op, not an error.
    """
    handler = FailJobRepositoryHandler()
    gctx = MagicMock()
    gctx.job_repository.update_job_status = AsyncMock()
    job = _job("sse-internal", repository_job_id=None)
    job.repository_job_id = None

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    gctx.job_repository.update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_exception_logs_when_repository_update_fails() -> None:
    """A failure updating the repository is logged, not raised."""
    handler = FailJobRepositoryHandler()
    gctx = MagicMock()
    gctx.job_repository.update_job_status = AsyncMock(side_effect=RuntimeError("down"))
    job = _job("J")

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    assert job.status == "failed"
