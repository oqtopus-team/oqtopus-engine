from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from omegaconf import OmegaConf

from oqtopus_engine_core.framework import (
    EstimationResult,
    Job,
    JobContext,
    JobInfo,
    JobResult,
)
from oqtopus_engine_core.steps.estimator_step import EstimatorStep


@pytest.mark.asyncio
async def test_post_process_skips_when_result_is_precomputed() -> None:
    step = EstimatorStep(
        basis_gates=OmegaConf.create(
            ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
        )
    )
    step._stub = AsyncMock()
    step._stub.ReqEstimationPostProcess = AsyncMock()

    jctx = JobContext()
    jctx["estimation_job_info"] = SimpleNamespace(
        counts_list=[{"0": 1}],
        grouped_operators=[[["Z"]], [[1.0]]],
    )
    job = Job(
        job_id="job-zne",
        device_id="device-1",
        shots=100,
        job_type="estimation",
        job_info=JobInfo(
            program=["OPENQASM 3.0; include \"stdgates.inc\";"],
            result=JobResult(estimation=EstimationResult(exp_value=0.5, stds=0.1)),
        ),
        transpiler_info={},
        simulator_info={},
        mitigation_info={"zne": {"enabled": True}},
        status="ready",
    )

    await step.post_process(None, jctx, job)

    assert step._stub.ReqEstimationPostProcess.await_count == 0
