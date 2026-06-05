import datetime
import json
import os
from pathlib import Path
from typing import Any

import grpc
from oqtopus_engine_core.interfaces.sse_interface.v1 import sse_pb2, sse_pb2_grpc

from oqtopus_client.rest.models import (
    JobsJob,
    JobsJobInfo,
    JobsS3SubmitJobInfo,
    JobsSubmitJobRequest,
)


class SseRuntimeError(RuntimeError):
    """Error raised when SSE runtime execution fails."""


def submit_job(
    input_job: JobsSubmitJobRequest,
    upload_info: JobsS3SubmitJobInfo,
) -> JobsJob:
    """Submit a job from SSE Runtime.

    Args:
        input_job: The job data for the request.
        upload_info: The submitted upload information for the job.

    Returns:
        JobsJob | None: If a timeout occurs, it returns None. Otherwise, it
            returns the Job.

    Raises:
        OSError: If the job data is not set.
        SseRuntimeError: If the job execution fails.

    """
    # get gRPC server address from environment variables
    grpc_sse_engine_address = os.environ.get("SSE_ENGINE_ADDRESS", "localhost:52014")

    # get job data from environment variable
    job_json = os.environ.get("JOB_JSON")
    if not job_json:
        msg = "Could not get job data"
        raise OSError(msg)

    with grpc.insecure_channel(f"{grpc_sse_engine_address}") as channel:
        created = datetime.datetime.now(tz=datetime.UTC)
        stub = sse_pb2_grpc.SseEngineServiceStub(channel)
        req_json = _make_request(job_json, input_job, upload_info)
        # gRPC request
        request = sse_pb2.SseEngineRequest(job_json=json.dumps(req_json))
        response = stub.SseEngine(request)

        # make content of output file to pass the result to sserunner
        result_dict = _load_json_dict(response.job_json)
        ended = datetime.datetime.now(tz=datetime.UTC)
        job = _make_client_job(result_dict)
        job.submitted_at = created
        job.ready_at = created
        job.running_at = created
        job.ended_at = ended

        # write the content into result.json
        _log_result(result_dict, "result.json")

        # raise error if the job execution is failed
        if response.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {response.message or _job_message(job)}"  # noqa: E501
            raise SseRuntimeError(msg)
        if job.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {_job_message(job)}"  # noqa: E501
            raise SseRuntimeError(msg)
        return job


def _make_client_job(
    result_dict: dict[str, Any],
) -> JobsJob:
    # convert the result dict to a Job object for oqtopus-client
    job = JobsJob.from_dict(result_dict)
    # extract job_info from the result dict
    job.job_info = JobsJobInfo.from_dict(result_dict)
    if job is None:
        msg = "Could not parse job data"
        raise SseRuntimeError(msg)
    return job


def _make_request(
    job_json: str,
    input_job: JobsSubmitJobRequest,
    upload_info: JobsS3SubmitJobInfo,
) -> dict[str, Any]:

    job_dict = _load_json_dict(job_json)
    request = input_job.to_dict()
    request["job_id"] = job_dict["job_id"]  # set job_id of parent SSE job
    request["status"] = "ready"
    request["input"] = ""

    return {"submit_job_request": request, "upload_info": upload_info.to_dict()}


def _load_json_dict(raw_json: str | None) -> dict[str, Any]:
    if not raw_json:
        return {}

    loaded = json.loads(raw_json)
    if not isinstance(loaded, dict):
        msg = "Expected job data to be a JSON object"
        raise SseRuntimeError(msg)
    return loaded


def _job_message(job: JobsJob) -> str | None:
    if job.job_info is None:
        return None
    return job.job_info.message


def _log_result(contents_dict: dict[str, Any], filename: str) -> None:
    # write the result to a file
    dir_path = os.environ.get("OUT_PATH", "")
    p = Path(dir_path)
    if not p.exists():
        msg = f"The path to output does not exist {dir_path}"
        raise SseRuntimeError(msg)

    file_path = Path(dir_path, filename)
    with Path.open(file_path, "w") as f:
        json.dump(contents_dict, f, indent=2, default=_json_default)


def _json_default(value: object) -> str:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    msg = f"Object of type {type(value).__name__} is not JSON serializable"
    raise TypeError(msg)
