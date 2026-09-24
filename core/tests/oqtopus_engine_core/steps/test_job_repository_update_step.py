from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import pytest

from oqtopus_engine_core.framework import Job, JobContext, JobResult, SamplingResult
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    JobsJobInfoUploadPresignedURL,
    JobsJobInfoUploadPresignedURLFields,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.repositories.oqtopus_cloud_job_repository import (
    OqtopusCloudJobRepository,
)
from oqtopus_engine_core.steps.job_repository_update_step import JobRepositoryUpdateStep


def _make_job() -> Job:
    return Job(
        job_id="job-1",
        repository_job_id="job-1",
        name="sse",
        description="",
        device_id="qulacs",
        shots=1,
        job_type="sse",
        input="input.zip",
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
        result=JobResult(sampling=SamplingResult(counts={"00": 1})),
        sse_log="line 1\nline 2\n",
        output_files=[],
    )


@pytest.mark.asyncio
async def test_post_process_uses_configured_sse_log_filename() -> None:
    step = JobRepositoryUpdateStep()
    job = _make_job()
    jctx = JobContext()
    gctx = SimpleNamespace(
        config={
            "di_container": {
                "registry": {
                    "sse_step": {
                        "runner_settings": {"log_file_name": "ssecontainer.log"}
                    }
                }
            }
        },
        job_repository=SimpleNamespace(
            upload_job_outputs=AsyncMock(),
            update_job_status_ordered=AsyncMock(),
        ),
    )

    await step.post_process(gctx, jctx, job)

    gctx.job_repository.upload_job_outputs.assert_awaited_once_with(
        job=job,
        outputs=[
            ("result", job.result.model_dump(), ".json", None),
            ("sse_log", job.sse_log, ".log", "ssecontainer.log"),
        ],
    )


@pytest.mark.asyncio
async def test_post_process_uploads_to_file_urls(tmp_path: Path) -> None:
    """Final job outputs can be uploaded through local file presigned URLs."""
    step = JobRepositoryUpdateStep()
    job = _make_job()
    result_path = tmp_path / "result.zip"
    log_path = tmp_path / "sse_log.zip"

    with (
        patch("oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.JobsApi"),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.ApiClient"
        ),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_job_repository.Configuration"
        ),
    ):
        repository = OqtopusCloudJobRepository(workers=2)

    repository._jobs_api.get_upload_with_http_info.return_value = (  # noqa: SLF001
        [
            JobsJobInfoUploadPresignedURL(
                url=result_path.as_uri(),
                fields=JobsJobInfoUploadPresignedURLFields(key="job-1/result.zip"),
            ),
            JobsJobInfoUploadPresignedURL(
                url=log_path.as_uri(),
                fields=JobsJobInfoUploadPresignedURLFields(key="job-1/sse_log.zip"),
            ),
        ],
        200,
        {},
    )
    repository.update_job_status_ordered = AsyncMock()  # type: ignore[method-assign]
    gctx = SimpleNamespace(
        config={
            "di_container": {
                "registry": {
                    "sse_step": {"runner_settings": {"log_file_name": "sse.log"}}
                }
            }
        },
        job_repository=repository,
    )

    await step.post_process(gctx, JobContext(), job)

    repository._jobs_api.get_upload_with_http_info.assert_called_once_with(  # noqa: SLF001
        job_id="job-1",
        items="result,sse_log",
        _request_timeout=10,
    )
    with ZipFile(result_path) as archive:
        assert archive.read("result.json")
    with ZipFile(log_path) as archive:
        assert archive.read("sse.log") == job.sse_log.encode()


def _make_gctx(job_repository: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        config={"di_container": {"registry": {}}},
        job_repository=job_repository,
    )


@pytest.mark.asyncio
async def test_post_process_falls_back_to_failed_status_on_update_failure() -> None:
    """If reporting 'succeeded' fails, the step must fall back to 'failed'."""
    step = JobRepositoryUpdateStep()
    job = _make_job()
    statuses_when_called: list[str] = []

    async def fake_update(job_arg: Job, *, include_output_files: bool = True) -> None:
        statuses_when_called.append(job_arg.status)
        if len(statuses_when_called) == 1:
            message = "succeeded update failed"
            raise RuntimeError(message)

    update_mock = AsyncMock(side_effect=fake_update)
    gctx = _make_gctx(
        SimpleNamespace(
            upload_job_outputs=AsyncMock(),
            update_job_status_ordered=update_mock,
        )
    )

    await step.post_process(gctx, JobContext(), job)

    assert statuses_when_called == ["succeeded", "failed"]
    assert job.status == "failed"
    assert "engine could not report succeeded status" in (job.message or "")
    assert update_mock.await_count == 2
    _, second_kwargs = update_mock.await_args_list[1]
    assert second_kwargs.get("include_output_files") is False


@pytest.mark.asyncio
async def test_post_process_logs_when_fallback_to_failed_also_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If both the 'succeeded' and the fallback 'failed' update fail, log it."""
    step = JobRepositoryUpdateStep()
    job = _make_job()
    update_mock = AsyncMock(side_effect=RuntimeError("still failing"))
    gctx = _make_gctx(
        SimpleNamespace(
            upload_job_outputs=AsyncMock(),
            update_job_status_ordered=update_mock,
        )
    )

    with caplog.at_level("ERROR"):
        await step.post_process(gctx, JobContext(), job)

    assert update_mock.await_count == 2
    assert any(
        "failed to fall back to failed status" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_post_process_fallback_conflict_is_not_logged_as_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 409 on the fallback PATCH means the job is already terminal elsewhere.

    This is expected (not an engine-side failure), so it must not be logged
    at ERROR level.
    """
    step = JobRepositoryUpdateStep()
    job = _make_job()
    conflict = ApiException(status=HTTPStatus.CONFLICT, reason="Conflict")
    update_mock = AsyncMock(
        side_effect=[RuntimeError("succeeded update failed"), conflict]
    )
    gctx = _make_gctx(
        SimpleNamespace(
            upload_job_outputs=AsyncMock(),
            update_job_status_ordered=update_mock,
        )
    )

    with caplog.at_level("INFO"):
        await step.post_process(gctx, JobContext(), job)

    assert update_mock.await_count == 2
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert not any(
        "failed to fall back to failed status" in r.getMessage()
        for r in error_records
    )
    assert any(
        "fallback to failed status rejected" in r.getMessage()
        and r.levelname == "INFO"
        for r in caplog.records
    )
