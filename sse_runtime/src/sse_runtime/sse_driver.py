import datetime
import json
import os
from pathlib import Path
from typing import Any

import grpc
from oqtopus_engine_core.interfaces.oqtopus_cloud.jobs_job import JobsJob
from oqtopus_engine_core.interfaces.sse_interface.v1 import sse_pb2, sse_pb2_grpc
from quri_parts.backend import BackendError

from quri_parts_oqtopus.rest import (
    JobsSubmitJobRequest,
)
from quri_parts_oqtopus.rest.models.jobs_estimation_result import JobsEstimationResult
from quri_parts_oqtopus.rest.models.jobs_job_def import JobsJobDef
from quri_parts_oqtopus.rest.models.jobs_job_info import JobsJobInfo
from quri_parts_oqtopus.rest.models.jobs_sampling_result import JobsSamplingResult
from quri_parts_oqtopus.rest.models.jobs_transpile_result import JobsTranspileResult


def submit_job(
        input_job: JobsSubmitJobRequest,
) -> JobsJobDef:
    """Submit a job from SSE Runtime

    Args:
        input_job: The job data for the request.

    Returns:
        JobsJobDef | None: If a timeout occurs, it returns None. Otherwise, it
            returns the Job.

    Raises:
        OSError: If the job data is not set.
        BackendError: If the job execution fails.

    """
    # get gRPC server address from environment variables
    grpc_sse_engine_address = os.environ.get("SSE_ENGINE_ADDRESS", "localhost:52014")

    # get job data from environment variable
    job_json = os.environ.get("JOB_JSON")
    if not job_json:
        msg = "Could not get job data"
        raise OSError(msg)

    with grpc.insecure_channel(f"{grpc_sse_engine_address}") as channel:
        created = datetime.datetime.now(tz=datetime.UTC) \
                                    .strftime("%Y-%m-%d %H:%M:%S")
        stub = sse_pb2_grpc.SseEngineServiceStub(channel)
        req_json = _make_request(job_json, input_job)
        # gRPC request
        request = sse_pb2.SseEngineRequest(job_json=json.dumps(req_json))
        response = stub.SseEngine(request)

        # make content of output file to pass the result to sserunner
        ended = datetime.datetime.now(tz=datetime.UTC) \
                        .strftime("%Y-%m-%d %H:%M:%S")
        job = _make_job_def(response)
        job.submitted_at = created
        job.ready_at = created
        job.running_at = created
        job.ended_at = ended

        # write the content into result.json
        _log_result(_make_resultjson(job), "result.json")

        # raise error if the job execution is failed
        if response.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {response.message}"  # noqa: E501
            raise BackendError(msg)
        if job.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {job.job_info.message}"  # noqa: E501
            raise BackendError(msg)

        return job


def _make_job_def(
    response: sse_pb2.SseEngineResponse,
) -> JobsJobDef:
    result_dict = json.loads(response.job_json or "{}")
    result_dict["name"] = result_dict["name"] or ""

    # Filter out unsupported fields to ensure compatibility with JobsJobDef
    for key in ["parent", "children"]:
        result_dict.pop(key, None)
    # Convert to JobsJobDef
    job = JobsJobDef(**result_dict)
    # Convert job_info and its nested objects
    job.job_info = JobsJobInfo(**result_dict.get("job_info", {}))
    job.job_info.result = {
        "sampling":
                JobsSamplingResult(
                    **(job.job_info.result or {}).get("sampling", {})
                )
                if job.job_type in {"sampling", "multi_manual"}
                else None,
        "estimation":
                JobsEstimationResult(
                    **(job.job_info.result or {}).get("estimation", {})
                )
                if job.job_type == "estimation"
                else None,
    }
    # Convert transpile_result if exists
    transpile_result = job.job_info.transpile_result
    if transpile_result:
        job.job_info.transpile_result = JobsTranspileResult(**transpile_result)
    return job


def _make_resultjson(job: JobsJobDef) -> dict[str, Any]:
    # convert the job data in order to make it readable in engine
    return job.to_dict()


def _make_request(
        job_json: str,
        input_job: JobsSubmitJobRequest,
) -> dict[str, Any]:
    job_dict = json.loads(job_json)  # parent SSE job
    request = JobsJob(**input_job.to_dict())
    request.job_id = job_dict["job_id"]  # set job_id of parent SSE job
    request.status = "ready"
    return request.to_dict()


def _log_result(contents_dict: dict[str, Any], filename: str) -> None:
    # write the result to a file
    dir_path = os.environ.get("OUT_PATH", "")
    p = Path(dir_path)
    if not p.exists():
        msg = f"The path to output does not exist {dir_path}"
        raise BackendError(msg)

    file_path = Path(dir_path, filename)
    with Path.open(file_path, "w") as f:
        json.dump(contents_dict, f, indent=2)
