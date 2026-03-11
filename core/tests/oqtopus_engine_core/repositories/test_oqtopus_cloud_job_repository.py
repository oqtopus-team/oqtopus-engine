import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from oqtopus_engine_core.framework.model import Job, JobInfo
from oqtopus_engine_core.repositories.oqtopus_cloud_job_repository import (
    OqtopusCloudJobRepository,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_test_job(job_id: str = "job-1") -> Job:
    """Minimal Job instance for use in repository unit tests."""
    return Job(
        job_id=job_id,
        job_type="sampling",
        device_id="test-device",
        shots=1,
        job_info=JobInfo(program=[]),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
    )


def _close_coroutine(coroutine: object) -> None:
    """Close a coroutine to suppress 'was never awaited' ResourceWarnings."""
    if hasattr(coroutine, "close"):
        coroutine.close()  # type: ignore[union-attr]


def make_repo() -> OqtopusCloudJobRepository:
    """Create an OqtopusCloudJobRepository with patched HTTP clients."""
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.JobApi"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.JobsApi"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.ApiClient"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.Configuration"
    ):
        return OqtopusCloudJobRepository(workers=2)


# ---------------------------------------------------------------------------
# _enqueue_and_run – ordering guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_run_executes_in_order():
    """Coroutines for the same job_id must run in FIFO order."""
    repo = make_repo()
    order: list[int] = []

    async def coro(n: int) -> None:
        order.append(n)

    # Enqueue three tasks for the same job_id
    t1 = asyncio.create_task(repo._enqueue_and_run("job-1", coro(1)))
    t2 = asyncio.create_task(repo._enqueue_and_run("job-1", coro(2)))
    t3 = asyncio.create_task(repo._enqueue_and_run("job-1", coro(3)))

    await asyncio.gather(t1, t2, t3)

    assert order == [1, 2, 3]


# ---------------------------------------------------------------------------
# _enqueue_and_run – queue cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_run_cleans_up_queue_after_completion():
    """The per-job queue must be removed once all tasks finish."""
    repo = make_repo()

    async def coro() -> None:
        pass

    await repo._enqueue_and_run("job-x", coro())

    assert "job-x" not in repo._job_queues


# ---------------------------------------------------------------------------
# _enqueue_and_run – independent job_ids do not interfere
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_run_different_job_ids_are_independent():
    """Tasks for different job IDs must not block each other."""
    repo = make_repo()
    results: list[str] = []

    async def coro(label: str) -> None:
        results.append(label)

    await asyncio.gather(
        repo._enqueue_and_run("job-a", coro("a")),
        repo._enqueue_and_run("job-b", coro("b")),
    )

    assert set(results) == {"a", "b"}


# ---------------------------------------------------------------------------
# update_job_status_nowait – use_job_queue=True (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_nowait_uses_queue_by_default():
    """update_job_status_nowait should route through _enqueue_and_run by default."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)
        # Close the coroutine to avoid ResourceWarning about it never being awaited
        _close_coroutine(coroutine)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]

    await repo.update_job_status_nowait(job)

    # Allow the background task to run
    await asyncio.sleep(0)

    assert enqueued == [job.job_id]


# ---------------------------------------------------------------------------
# update_job_status_nowait – use_job_queue=False (bypass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_nowait_bypasses_queue_when_false():
    """update_job_status_nowait should NOT use _enqueue_and_run when use_job_queue=False."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]
    repo.update_job_status = AsyncMock()  # type: ignore[method-assign]

    await repo.update_job_status_nowait(job, use_job_queue=False)

    # Allow the background task to run
    await asyncio.sleep(0)

    assert enqueued == []
    repo.update_job_status.assert_awaited_once_with(job)


# ---------------------------------------------------------------------------
# update_job_info_nowait – use_job_queue=True (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_info_nowait_uses_queue_by_default():
    """update_job_info_nowait should route through _enqueue_and_run by default."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)
        _close_coroutine(coroutine)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]

    await repo.update_job_info_nowait(job)

    await asyncio.sleep(0)

    assert enqueued == [job.job_id]


# ---------------------------------------------------------------------------
# update_job_info_nowait – use_job_queue=False (bypass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_info_nowait_bypasses_queue_when_false():
    """update_job_info_nowait should NOT use _enqueue_and_run when use_job_queue=False."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]
    repo.update_job_info = AsyncMock()  # type: ignore[method-assign]

    await repo.update_job_info_nowait(job, use_job_queue=False)

    await asyncio.sleep(0)

    assert enqueued == []
    repo.update_job_info.assert_awaited_once_with(
        job, overwrite_status=None, execution_time=None
    )


# ---------------------------------------------------------------------------
# update_job_transpiler_info_nowait – use_job_queue=True (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_transpiler_info_nowait_uses_queue_by_default():
    """update_job_transpiler_info_nowait routes through _enqueue_and_run by default."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)
        _close_coroutine(coroutine)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]

    await repo.update_job_transpiler_info_nowait(job)

    await asyncio.sleep(0)

    assert enqueued == [job.job_id]


# ---------------------------------------------------------------------------
# update_job_transpiler_info_nowait – use_job_queue=False (bypass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_transpiler_info_nowait_bypasses_queue_when_false():
    """update_job_transpiler_info_nowait skips queue when use_job_queue=False."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]
    repo.update_job_transpiler_info = AsyncMock()  # type: ignore[method-assign]

    await repo.update_job_transpiler_info_nowait(job, use_job_queue=False)

    await asyncio.sleep(0)

    assert enqueued == []
    repo.update_job_transpiler_info.assert_awaited_once_with(job)


# ---------------------------------------------------------------------------
# update_sselog_nowait – use_job_queue=True (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_sselog_nowait_uses_queue_by_default():
    """update_sselog_nowait should route through _enqueue_and_run by default."""
    repo = make_repo()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)
        _close_coroutine(coroutine)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]

    await repo.update_sselog_nowait("job-1", "log content")

    await asyncio.sleep(0)

    assert enqueued == ["job-1"]


# ---------------------------------------------------------------------------
# update_sselog_nowait – use_job_queue=False (bypass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_sselog_nowait_bypasses_queue_when_false():
    """update_sselog_nowait skips queue when use_job_queue=False."""
    repo = make_repo()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]
    repo.update_sselog = AsyncMock()  # type: ignore[method-assign]

    await repo.update_sselog_nowait("job-1", "log content", use_job_queue=False)

    await asyncio.sleep(0)

    assert enqueued == []
    repo.update_sselog.assert_awaited_once_with("job-1", "log content")
