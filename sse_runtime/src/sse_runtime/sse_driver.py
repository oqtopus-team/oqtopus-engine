import datetime
import json
import os
from pathlib import Path
from typing import Any

import grpc
from oqtopus_client.rest.models import (
    JobsJob,
    JobsJobInfo,
    JobsS3SubmitJobInfo,
    JobsSubmitJobRequest,
)
from oqtopus_engine_core.interfaces.sse_interface.v1 import sse_pb2, sse_pb2_grpc
from pydantic import ValidationError

from sse_runtime.observability import setup_observability

# Wire the OTel parent context / job baggage as soon as the runtime package
# is imported by the user program, so every span (including the
# auto-instrumented gRPC client span for SseEngine) is parented under the
# engine's per-job root span.
setup_observability()


class SseRuntimeError(RuntimeError):
    """Error raised when SSE runtime execution fails."""


def submit_job(
    input_job: JobsSubmitJobRequest,
    upload_info: JobsS3SubmitJobInfo,
) -> JobsJob:
    """Submit a job to the SSE engine.

    Args:
        input_job: The job data for the request.
        upload_info: The submitted upload information for the job.

    Returns:
        JobsJob | None: If a timeout occurs, it returns None. Otherwise, it
            returns the OQTOPUS Client Job.

    Raises:
        OSError: If the job data is not set.
        SseRuntimeError: If the job execution fails.

    """
    # get gRPC server address from environment variables
    grpc_sse_engine_address = os.environ.get("SSE_ENGINE_ADDRESS", "localhost:52014")
    grpc_max_receive = int(
        os.environ.get(
            "GRPC_MAX_RECEIVE_MESSAGE_LENGTH",
            os.environ.get("GRPC_MAX_MESSAGE_BYTES", str(4 * 1024 * 1024)),
        )
    )
    grpc_max_send = int(
        os.environ.get(
            "GRPC_MAX_SEND_MESSAGE_LENGTH",
            os.environ.get("GRPC_MAX_MESSAGE_BYTES", str(4 * 1024 * 1024)),
        )
    )

    # get job data from environment variable
    job_json = os.environ.get("JOB_JSON")
    if not job_json:
        msg = "Could not get job data"
        raise OSError(msg)

    grpc_options = [
        ("grpc.max_receive_message_length", grpc_max_receive),
        ("grpc.max_send_message_length", grpc_max_send),
    ]
    with grpc.insecure_channel(
        grpc_sse_engine_address,
        options=grpc_options,
    ) as channel:
        created = _now_utc()
        stub = sse_pb2_grpc.SseEngineServiceStub(channel)
        request_dict = _make_request(job_json, input_job, upload_info)
        # gRPC request
        request = sse_pb2.SseEngineRequest(job_json=json.dumps(request_dict))
        response = stub.SseEngine(request)

        # make an OQTOPUS Client Job object from the response to return to the caller
        job = _convert_to_oqtopus_client_job_model(response.job_json)
        _set_execution_timestamps(job, created=created, ended=_now_utc())

        # write the result to a file for sserunner to read
        _log_result(response.job_json, "result.json")

        # raise error if the job execution is failed
        if response.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {response.message or _job_message(job)}"  # noqa: E501
            raise SseRuntimeError(msg)
        if job.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {_job_message(job)}"  # noqa: E501
            raise SseRuntimeError(msg)
        return job


def _make_request(
    job_json: str,
    input_job: JobsSubmitJobRequest,
    upload_info: JobsS3SubmitJobInfo,
) -> dict[str, Any]:
    request = _convert_to_engine_job_model(input_job, upload_info)
    # set job_id of parent SSE job and status
    request["job_id"] = _load_json_dict(job_json).get("job_id") or ""
    request["status"] = "ready"
    return request


def _convert_to_engine_job_model(
    input_job: JobsSubmitJobRequest,
    upload_info: JobsS3SubmitJobInfo,
) -> dict[str, Any]:
    engine_job_dict = input_job.model_dump()
    upload_info_dict = upload_info.model_dump()

    engine_job_dict["name"] = engine_job_dict["name"] or ""
    engine_job_dict["program"] = upload_info_dict.get("program") or []
    engine_job_dict["operator"] = upload_info_dict.get("operator") or []
    engine_job_dict["input"] = ""
    engine_job_dict["transpiler_info"] = engine_job_dict.get("transpiler_info") or {}
    engine_job_dict["simulator_info"] = engine_job_dict.get("simulator_info") or {}
    engine_job_dict["mitigation_info"] = engine_job_dict.get("mitigation_info") or {}
    return engine_job_dict


def _convert_to_oqtopus_client_job_model(
    response_json: str,
) -> JobsJob:
    try:
        # convert the response to a Job object for OQTOPUS Client
        job = JobsJob.model_validate_json(response_json, extra="ignore")
        # extract job_info from the response
        job.job_info = JobsJobInfo.model_validate_json(response_json, extra="ignore")
    except ValidationError as e:
        msg = f"Could not parse job data: {e}"
        raise SseRuntimeError(msg) from e
    return job


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


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _set_execution_timestamps(
    job: JobsJob,
    *,
    created: datetime.datetime,
    ended: datetime.datetime,
) -> None:
    job.submitted_at = created
    job.ready_at = created
    job.running_at = created
    job.ended_at = ended


def _log_result(contents_json: str, filename: str) -> None:
    # write the result to a file
    dir_path = os.environ.get("OUT_PATH", "")
    p = Path(dir_path)
    if not p.exists():
        msg = f"The path to output does not exist {dir_path}"
        raise SseRuntimeError(msg)

    file_path = Path(dir_path, filename)
    with Path.open(file_path, "w") as f:
        f.write(contents_json)
