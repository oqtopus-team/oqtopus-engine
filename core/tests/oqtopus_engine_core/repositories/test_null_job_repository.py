import pytest

from oqtopus_engine_core.framework import Job
from oqtopus_engine_core.repositories.null_job_repository import NullJobRepository


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
async def test_get_jobs_returns_empty_list() -> None:
    repository = NullJobRepository()

    jobs = await repository.get_jobs(device_id="Kawasaki")

    assert jobs == []


@pytest.mark.asyncio
async def test_get_job_upload_urls_returns_placeholder_urls() -> None:
    repository = NullJobRepository()
    job = _make_job()

    urls = await repository.get_job_upload_urls(job, ["transpile_result", "result"])

    assert [url.url for url in urls] == ["null://upload", "null://upload"]
    assert [url.fields.key for url in urls] == [
        "job-1/transpile_result",
        "job-1/result",
    ]


@pytest.mark.asyncio
async def test_download_job_input_returns_empty_dict() -> None:
    repository = NullJobRepository()

    downloaded = await repository.download_job_input(_make_job())

    assert downloaded == {}


@pytest.mark.asyncio
async def test_upload_job_outputs_does_not_mutate_output_files() -> None:
    repository = NullJobRepository()
    job = _make_job()

    await repository.upload_job_outputs(
        job=job,
        outputs=[("result", {"ok": True}, ".json", None)],
    )

    assert job.output_files == []
