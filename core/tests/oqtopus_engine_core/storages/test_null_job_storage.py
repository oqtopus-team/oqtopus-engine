import pytest

from oqtopus_engine_core.framework import Job
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    JobsJobInfoUploadPresignedURL,
    JobsJobInfoUploadPresignedURLFields,
)
from oqtopus_engine_core.storages.null_job_storage import NullJobStorage


def _make_job() -> Job:
    return Job(
        job_id="job-1",
        device_id="Kawasaki",
        shots=100,
        job_type="sampling",
        input="http://example.invalid/input.zip",
        program=["OPENQASM 3.0;\n"],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
        output_files=[],
    )


@pytest.mark.asyncio
async def test_download_job_input_returns_empty_dict() -> None:
    storage = NullJobStorage()

    downloaded = await storage.download_job_input(_make_job())

    assert downloaded == {}


@pytest.mark.asyncio
async def test_upload_job_output_does_not_mutate_output_files() -> None:
    storage = NullJobStorage()
    job = _make_job()
    presigned_url = JobsJobInfoUploadPresignedURL(
        url="null://upload",
        fields=JobsJobInfoUploadPresignedURLFields(key="job-1/result"),
    )

    await storage.upload_job_output(
        job=job,
        presigned_url=presigned_url,
        data={"ok": True},
        arcname_ext=".json",
    )

    assert job.output_files == []
