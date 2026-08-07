from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oqtopus_engine_core.framework import Job, JobContext, JobResult, SamplingResult
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
