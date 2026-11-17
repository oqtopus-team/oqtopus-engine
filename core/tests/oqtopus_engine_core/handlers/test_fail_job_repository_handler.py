import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from oqtopus_engine_core.framework import JobContext
from oqtopus_engine_core.framework.model import Job
from oqtopus_engine_core.handlers.fail_job_repository_handler import (
    FailJobRepositoryHandler,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.repositories.oqtopus_cloud_job_repository import (
    OqtopusCloudJobRepository,
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


def _gctx(**ordered_kwargs: object) -> MagicMock:
    """A GlobalContext whose repository records both PATCH entry points.

    `update_job_status` is mocked too so each test can assert the handler
    never bypasses the per-job queue by calling it directly.
    """
    gctx = MagicMock()
    gctx.job_repository.update_job_status_ordered = AsyncMock(**ordered_kwargs)
    gctx.job_repository.update_job_status = AsyncMock()
    return gctx


@pytest.mark.asyncio
async def test_handle_exception_ordinary_job_fails_itself_with_original_message() -> (
    None
):
    """1:1 — an ordinary job fails itself with the original exception message."""
    handler = FailJobRepositoryHandler()
    gctx = _gctx()
    job = _job("J")

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    assert job.status == "failed"
    assert job.message == "boom"
    gctx.job_repository.update_job_status_ordered.assert_awaited_once_with(job)
    gctx.job_repository.update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_exception_estimation_child_fails_parent_only() -> None:
    """N:1 — an exception on an estimation child fails the parent, not the
    child (the child has no Cloud record of its own).
    """
    handler = FailJobRepositoryHandler()
    gctx = _gctx()

    parent = _job("P")
    child = _job("P-estimation-0", repository_job_id="P")
    child.parent = parent
    parent.children = [child]

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), child)

    assert parent.status == "failed"
    assert parent.message == "boom"
    gctx.job_repository.update_job_status_ordered.assert_awaited_once_with(parent)
    gctx.job_repository.update_job_status.assert_not_awaited()


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
    gctx = _gctx()

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
    assert gctx.job_repository.update_job_status_ordered.await_count == 2
    gctx.job_repository.update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_exception_empty_resolution_updates_nothing() -> None:
    """An internal job with no repository entity and no children to
    delegate to (SSE-internal shape) is a no-op, not an error.
    """
    handler = FailJobRepositoryHandler()
    gctx = _gctx()
    job = _job("sse-internal", repository_job_id=None)
    job.repository_job_id = None

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    gctx.job_repository.update_job_status_ordered.assert_not_awaited()
    gctx.job_repository.update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_exception_logs_when_repository_update_fails() -> None:
    """A failure updating the repository is logged, not raised."""
    handler = FailJobRepositoryHandler()
    gctx = _gctx(side_effect=RuntimeError("down"))
    job = _job("J")

    await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    assert job.status == "failed"
    gctx.job_repository.update_job_status_ordered.assert_awaited_once_with(job)
    gctx.job_repository.update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_exception_conflict_is_not_logged_as_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 409 means the job is already terminal elsewhere (e.g. cancelled by
    the user). This is expected, so it is logged at INFO, not ERROR, and is
    not raised.
    """
    handler = FailJobRepositoryHandler()
    conflict = ApiException(status=HTTPStatus.CONFLICT, reason="Conflict")
    gctx = _gctx(side_effect=conflict)
    job = _job("J")

    with caplog.at_level("INFO"):
        await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    gctx.job_repository.update_job_status_ordered.assert_awaited_once_with(job)
    assert not any(
        "failed to update job status to 'failed'" in r.getMessage()
        for r in caplog.records
        if r.levelname == "ERROR"
    )
    assert any(
        "failed status rejected; job already terminal" in r.getMessage()
        and r.levelname == "INFO"
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_handle_exception_non_conflict_api_error_is_logged_as_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An ApiException other than 409 is still an engine-side failure to
    report the status, so it stays at ERROR (and is still not raised).
    """
    handler = FailJobRepositoryHandler()
    bad_request = ApiException(status=HTTPStatus.BAD_REQUEST, reason="Bad Request")
    gctx = _gctx(side_effect=bad_request)
    job = _job("J")

    with caplog.at_level("INFO"):
        await handler.handle_exception(RuntimeError("boom"), gctx, JobContext(), job)

    assert any(
        "failed to update job status to 'failed'" in r.getMessage()
        and r.levelname == "ERROR"
        for r in caplog.records
    )
    assert not any("job already terminal" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# FailJobRepositoryHandler – the terminal failed PATCH is queued behind the
# output upload and the ready -> running PATCH already queued for the job.
# Reproduces the production race where the handler's failed PATCH was sent
# directly, overtook both, carried no output_files, and made the running
# PATCH fail with 409.
# ---------------------------------------------------------------------------


def _make_cloud_repo() -> OqtopusCloudJobRepository:
    """Create a real OqtopusCloudJobRepository with patched HTTP clients."""
    with (
        patch("oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.JobsApi"),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.ApiClient"
        ),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.Configuration"
        ),
    ):
        return OqtopusCloudJobRepository(workers=2)


def _prepare_upload_and_patch_mocks(
    repo: OqtopusCloudJobRepository,
    *,
    upload_key: str,
    storage_call: Callable[[], Awaitable[None]],
) -> list[tuple[str, list[str] | None]]:
    """Mock the Cloud API and storage; return the PATCH bodies sent so far."""
    presigned_url = MagicMock()
    presigned_url.url = "https://storage.example/upload"
    presigned_url.fields.key = upload_key
    repo._jobs_api.get_upload_with_http_info.return_value = (  # type: ignore[attr-defined]
        [presigned_url],
        200,
        {},
    )

    async def fake_storage_request(*_args: object, **_kwargs: object) -> None:
        await storage_call()

    repo._storage_request_with_error_logging = fake_storage_request  # type: ignore[method-assign]

    patched: list[tuple[str, list[str] | None]] = []

    def fake_patch_job_with_http_info(**kwargs: object) -> tuple[object, int, dict]:
        body = kwargs["body"]
        patched.append((body.status, body.output_files))  # type: ignore[attr-defined]
        return None, 200, {}

    repo._jobs_api.patch_job_with_http_info = fake_patch_job_with_http_info  # type: ignore[attr-defined]
    return patched


@pytest.mark.asyncio
async def test_fail_handler_patch_waits_for_queued_upload_and_running_patch():
    repo = _make_cloud_repo()
    job = _job("job-1")
    job.status = "ready"
    upload_started = asyncio.Event()
    proceed = asyncio.Event()

    async def blocked_storage_upload() -> None:
        upload_started.set()
        await proceed.wait()

    patched = _prepare_upload_and_patch_mocks(
        repo,
        upload_key="job-1/transpile_result.zip",
        storage_call=blocked_storage_upload,
    )
    gctx = MagicMock()
    gctx.job_repository = repo

    # TranquStep: transpile_result upload queued on the per-job chain.
    await repo.upload_job_outputs_nowait(
        job, [("transpile_result", {"k": "v"}, "", None)]
    )
    await upload_started.wait()
    # DeviceGatewayStep: ready -> running queued behind the upload.
    job.status = "running"
    await repo.update_job_status_nowait(job, include_output_files=False)

    # The device call fails while the upload is still in flight.
    handler_task = asyncio.create_task(
        FailJobRepositoryHandler().handle_exception(
            RuntimeError("device failed"), gctx, JobContext(), job
        )
    )
    for _ in range(5):
        await asyncio.sleep(0)

    # Neither PATCH may be sent before the upload completes.
    assert patched == []

    proceed.set()
    await handler_task
    await asyncio.gather(*repo._background_requests)

    assert patched == [
        ("running", None),
        ("failed", ["job-1/transpile_result.zip"]),
    ]


@pytest.mark.asyncio
async def test_fail_handler_patch_is_sent_without_key_when_upload_fails():
    repo = _make_cloud_repo()
    job = _job("job-1")
    job.status = "ready"
    upload_started = asyncio.Event()
    proceed = asyncio.Event()

    async def failing_storage_upload() -> None:
        upload_started.set()
        await proceed.wait()
        msg = "storage down"
        raise RuntimeError(msg)

    patched = _prepare_upload_and_patch_mocks(
        repo,
        upload_key="job-1/transpile_result.zip",
        storage_call=failing_storage_upload,
    )
    gctx = MagicMock()
    gctx.job_repository = repo

    await repo.upload_job_outputs_nowait(
        job, [("transpile_result", {"k": "v"}, "", None)]
    )
    await upload_started.wait()
    job.status = "running"
    await repo.update_job_status_nowait(job, include_output_files=False)

    handler_task = asyncio.create_task(
        FailJobRepositoryHandler().handle_exception(
            RuntimeError("device failed"), gctx, JobContext(), job
        )
    )
    proceed.set()
    await handler_task
    await asyncio.gather(*repo._background_requests, return_exceptions=True)

    # A failed upload does not block the chain, and its key is never added.
    assert patched == [("running", None), ("failed", [])]
