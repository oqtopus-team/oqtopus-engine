import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.repositories.oqtopus_cloud_job_repository import (
    OqtopusCloudJobRepository,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_test_job(job_id: str = "job-1") -> Job:
    """Minimal Job instance for use in repository unit tests.

    `repository_job_id` defaults to `job_id` (the fetcher-origin rule) so
    these jobs pass `_is_repository_tracked` like any ordinary Cloud-tracked
    job.
    """
    return Job(
        job_id=job_id,
        repository_job_id=job_id,
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


def _close_coroutine(coroutine: object) -> None:
    """Close a coroutine to suppress 'was never awaited' ResourceWarnings."""
    if hasattr(coroutine, "close"):
        coroutine.close()  # type: ignore[union-attr]


def make_repo() -> OqtopusCloudJobRepository:
    """Create an OqtopusCloudJobRepository with patched HTTP clients."""
    with patch(
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

    assert "job-x" not in repo._job_tails


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
# update_job_status_nowait – preserve_order=True (default)
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
# update_job_status_nowait – preserve_order=False (bypass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_nowait_bypasses_queue_when_false():
    """update_job_status_nowait should NOT use _enqueue_and_run when preserve_order=False."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]
    repo.update_job_status = AsyncMock()  # type: ignore[method-assign]

    await repo.update_job_status_nowait(job, preserve_order=False)

    # Allow the background task to run
    await asyncio.sleep(0)

    assert enqueued == []
    repo.update_job_status.assert_awaited_once_with(job, include_output_files=True)


# ---------------------------------------------------------------------------
# update_job_status_nowait – output_files is read at execution time, not at
# call time (regression test for the non-deterministic job_info loss bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_nowait_includes_output_files_appended_after_call():
    """A key appended to job.output_files after the call must still be sent.

    This reproduces the race between upload_job_outputs_nowait (which appends
    to job.output_files only once its upload completes) and
    update_job_status_nowait (which patches job status). Before the fix,
    update_job_status_nowait snapshotted output_files at call time via
    copy.deepcopy, so a key appended later - even before this task actually
    runs - was silently dropped from the PATCH body.
    """
    repo = make_repo()
    job = make_test_job()

    sent_bodies: list[object] = []

    async def fake_update_job_status(
        patched_job: Job, *, include_output_files: bool = True
    ) -> None:
        sent_bodies.append(
            list(patched_job.output_files) if include_output_files else None
        )

    repo.update_job_status = fake_update_job_status  # type: ignore[method-assign]

    # Call update_job_status_nowait first; the append below happens
    # "after the call" but before the FIFO-queued task actually executes.
    await repo.update_job_status_nowait(job)
    job.output_files.append("job-1/transpile_result.zip")

    # Drive the background task (and the _enqueue_and_run task it is nested
    # in) to completion.
    await asyncio.gather(*repo._background_requests)

    assert sent_bodies == [["job-1/transpile_result.zip"]]


# ---------------------------------------------------------------------------
# update_job_status / update_job_status_nowait – include_output_files=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_omits_output_files_when_excluded():
    """include_output_files=False must omit output_files from the request body."""
    repo = make_repo()
    job = make_test_job()
    job.output_files.append("job-1/result.zip")

    sent_bodies: list[object] = []

    def fake_patch_job_with_http_info(**kwargs: object) -> tuple[object, int, dict]:
        sent_bodies.append(kwargs["body"])
        return (None, 200, {})

    repo._jobs_api.patch_job_with_http_info = fake_patch_job_with_http_info  # type: ignore[method-assign]

    await repo.update_job_status(job, include_output_files=False)

    assert sent_bodies[0].output_files is None


@pytest.mark.asyncio
async def test_update_job_status_nowait_forwards_include_output_files_false():
    """update_job_status_nowait must forward include_output_files to update_job_status."""
    repo = make_repo()
    job = make_test_job()
    job.output_files.append("job-1/result.zip")
    repo.update_job_status = AsyncMock()  # type: ignore[method-assign]

    await repo.update_job_status_nowait(job, include_output_files=False)
    await asyncio.gather(*repo._background_requests)

    repo.update_job_status.assert_awaited_once()
    _, kwargs = repo.update_job_status.await_args
    assert kwargs["include_output_files"] is False


# ---------------------------------------------------------------------------
# upload_job_outputs_nowait – preserve_order=True (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_job_outputs_nowait_uses_queue_by_default():
    """Grouped output uploads should route through the per-job queue."""
    repo = make_repo()
    job = make_test_job()
    outputs = [("result", {"ok": True}, ".json", None)]

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)
        _close_coroutine(coroutine)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]

    await repo.upload_job_outputs_nowait(job, outputs)

    await asyncio.sleep(0)

    assert enqueued == [job.job_id]


# ---------------------------------------------------------------------------
# update_job_transpiler_info_nowait – preserve_order=True (default)
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
# update_job_transpiler_info_nowait – preserve_order=False (bypass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_transpiler_info_nowait_bypasses_queue_when_false():
    """update_job_transpiler_info_nowait skips queue when preserve_order=False."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[str] = []

    async def fake_enqueue(job_id: str, coroutine: object) -> None:
        enqueued.append(job_id)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]
    repo.update_job_transpiler_info = AsyncMock()  # type: ignore[method-assign]

    await repo.update_job_transpiler_info_nowait(job, preserve_order=False)

    await asyncio.sleep(0)

    assert enqueued == []
    repo.update_job_transpiler_info.assert_awaited_once_with(job)


# ---------------------------------------------------------------------------
# _enqueue_and_run – queue cleanup after coroutine failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_run_cleans_up_queue_after_failure():
    """The per-job queue entry must be removed even when the coroutine raises."""
    repo = make_repo()

    async def failing_coroutine() -> None:
        raise RuntimeError("intentional failure")

    # _enqueue_and_run catches internal exceptions; it should not propagate here.
    await repo._enqueue_and_run("job-fail", failing_coroutine())

    assert "job-fail" not in repo._job_tails


# ---------------------------------------------------------------------------
# _enqueue_and_run – loop continues after one coroutine fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_run_continues_after_coroutine_failure():
    """Subsequent coroutines for the same job_id must still run after one fails."""
    repo = make_repo()
    executed: list[str] = []

    async def failing_coroutine() -> None:
        raise RuntimeError("intentional failure")

    async def success_coroutine() -> None:
        executed.append("success")

    t1 = asyncio.create_task(repo._enqueue_and_run("job-seq", failing_coroutine()))
    t2 = asyncio.create_task(repo._enqueue_and_run("job-seq", success_coroutine()))

    await asyncio.gather(t1, t2)

    # The success coroutine must have run despite the earlier failure.
    assert "success" in executed
    # The queue entry must be cleaned up.
    assert "job-seq" not in repo._job_tails


# ---------------------------------------------------------------------------
# get_jobs – api_request_timeout_seconds is passed to the generated client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_jobs_passes_api_request_timeout_seconds():
    """get_jobs must forward api_request_timeout_seconds as _request_timeout."""
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.JobsApi"
    ) as mock_jobs_api_cls, patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.ApiClient"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.Configuration"
    ):
        mock_jobs_api = mock_jobs_api_cls.return_value
        mock_jobs_api.get_jobs_with_http_info.return_value = ([], 200, {})

        repo = OqtopusCloudJobRepository(workers=2, api_request_timeout_seconds=15)
        await repo.get_jobs(device_id="test-device")

        _, kwargs = mock_jobs_api.get_jobs_with_http_info.call_args
        assert kwargs["_request_timeout"] == 15


# ---------------------------------------------------------------------------
# _enqueue_and_run – failed coroutine is logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_run_logs_exception_on_failure(caplog: pytest.LogCaptureFixture):
    """Exceptions from coroutines must be logged, not silently swallowed."""
    import logging

    repo = make_repo()

    async def failing_coroutine() -> None:
        raise ValueError("logged failure")

    with caplog.at_level(logging.ERROR):
        await repo._enqueue_and_run("job-log", failing_coroutine())

    assert any(
        "task failed" in record.message and record.levelname == "ERROR"
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# _enqueue_and_run – propagate=True re-raises the coroutine's own failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_and_run_propagates_own_failure_when_requested():
    """With propagate=True, the coroutine's own exception reaches the caller."""
    repo = make_repo()

    async def failing_coroutine() -> None:
        message = "boom"
        raise ValueError(message)

    with pytest.raises(ValueError, match="boom"):
        await repo._enqueue_and_run("job-prop", failing_coroutine(), propagate=True)

    # Queue cleanup must still happen even when the exception propagates.
    assert "job-prop" not in repo._job_tails


@pytest.mark.asyncio
async def test_enqueue_and_run_default_does_not_propagate():
    """Without propagate=True (the default), the caller sees no exception."""
    repo = make_repo()

    async def failing_coroutine() -> None:
        message = "boom"
        raise ValueError(message)

    # Must not raise.
    await repo._enqueue_and_run("job-noprop", failing_coroutine())


@pytest.mark.asyncio
async def test_enqueue_and_run_propagate_does_not_reraise_previous_failure():
    """A previous failed task for the same job_id must not block this one.

    propagate=True only reflects the outcome of *this* coroutine, not of
    whatever ran before it in the same job_id's chain.
    """
    repo = make_repo()
    executed: list[str] = []

    async def failing_coroutine() -> None:
        message = "previous failure"
        raise RuntimeError(message)

    async def success_coroutine() -> None:
        executed.append("ran")

    t1 = asyncio.create_task(repo._enqueue_and_run("job-chain", failing_coroutine()))
    t2 = asyncio.create_task(
        repo._enqueue_and_run("job-chain", success_coroutine(), propagate=True)
    )

    await t1
    await t2  # must not raise, even though t1 failed

    assert executed == ["ran"]


# ---------------------------------------------------------------------------
# update_job_status_ordered – routes through the per-job queue with
# propagate=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_ordered_uses_queue_with_propagate():
    """update_job_status_ordered must route through _enqueue_and_run(propagate=True)."""
    repo = make_repo()
    job = make_test_job()

    enqueued: list[tuple[str, bool]] = []

    async def fake_enqueue(
        job_id: str, coroutine: object, *, propagate: bool = False
    ) -> None:
        enqueued.append((job_id, propagate))
        _close_coroutine(coroutine)

    repo._enqueue_and_run = fake_enqueue  # type: ignore[method-assign]

    await repo.update_job_status_ordered(job)

    assert enqueued == [(job.job_id, True)]


# ---------------------------------------------------------------------------
# update_job_status_ordered – propagates PATCH failures to the caller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_ordered_propagates_failure():
    """Unlike update_job_status_nowait, failures must reach the caller."""
    repo = make_repo()
    job = make_test_job()
    repo.update_job_status = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("patch failed")
    )

    with pytest.raises(RuntimeError, match="patch failed"):
        await repo.update_job_status_ordered(job)


# ---------------------------------------------------------------------------
# update_job_status_ordered – include_output_files=False is forwarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_ordered_forwards_include_output_files_false():
    """update_job_status_ordered must forward include_output_files."""
    repo = make_repo()
    job = make_test_job()
    job.output_files.append("job-1/result.zip")
    repo.update_job_status = AsyncMock()  # type: ignore[method-assign]

    await repo.update_job_status_ordered(job, include_output_files=False)

    repo.update_job_status.assert_awaited_once()
    _, kwargs = repo.update_job_status.await_args
    assert kwargs["include_output_files"] is False


# ---------------------------------------------------------------------------
# update_job_status_ordered – waits for a previously queued task for the same
# job_id before reading job.output_files (same FIFO guarantee as _nowait;
# regression test for the race fixed by PR #120, now also covered here since
# update_job_status_ordered must not bypass the per-job queue).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_status_ordered_waits_for_prior_queued_task():
    repo = make_repo()
    job = make_test_job()
    started = asyncio.Event()
    proceed = asyncio.Event()
    sent_bodies: list[object] = []

    async def fake_update_job_status(
        patched_job: Job, *, include_output_files: bool = True
    ) -> None:
        sent_bodies.append(
            list(patched_job.output_files) if include_output_files else None
        )

    repo.update_job_status = fake_update_job_status  # type: ignore[method-assign]

    async def prior_upload() -> None:
        started.set()
        await proceed.wait()
        job.output_files.append("job-1/transpile_result.zip")

    # Simulate a fire-and-forget output upload already queued for this job_id.
    prior_task = asyncio.create_task(
        repo._enqueue_and_run(job.job_id, prior_upload())
    )
    await started.wait()  # prior_upload has registered itself as the tail

    ordered_task = asyncio.create_task(repo.update_job_status_ordered(job))
    await asyncio.sleep(0)

    # The ordered update must not have run yet: it is queued behind prior_task.
    assert sent_bodies == []

    proceed.set()
    await prior_task
    await ordered_task

    assert sent_bodies == [["job-1/transpile_result.zip"]]
