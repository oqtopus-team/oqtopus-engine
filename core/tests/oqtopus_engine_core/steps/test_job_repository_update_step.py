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
from oqtopus_engine_core.repositories.oqtopus_cloud_job_repository import (
    OqtopusCloudJobRepository,
)
from oqtopus_engine_core.steps.job_repository_update_step import JobRepositoryUpdateStep


def _make_job() -> Job:
    return Job(
        job_id="job-1",
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
            update_job_status_nowait=AsyncMock(),
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
    repository.update_job_status_nowait = AsyncMock()  # type: ignore[method-assign]
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
