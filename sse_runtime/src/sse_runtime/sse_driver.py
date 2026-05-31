import datetime
import json
import os
from pathlib import Path
from typing import Any

import grpc
# from oqtopus_client.rest.models.jobs_job_def import JobsJobDef
from oqtopus_client.rest.models import JobsJob
from oqtopus_engine_core.interfaces.sse_interface.v1 import sse_pb2, sse_pb2_grpc

# from quri_parts_oqtopus.rest import (
#     JobsSubmitJobRequest,
# )
from oqtopus_client.rest.models import (
    JobsSubmitJobRequest,
    JobsS3SubmitJobInfo,
)

class SseRuntimeError(RuntimeError):
    """Error raised when SSE runtime execution fails."""


def submit_job(
        input_job: JobsSubmitJobRequest,
        upload_info: JobsS3SubmitJobInfo,
) -> JobsJob:
    """Submit a job from SSE Runtime

    Args:
        input_job: The job data for the request.

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
        ended = datetime.datetime.now(tz=datetime.UTC)
        job = _make_client_job(response, req_json)
        job.submitted_at = created
        job.ready_at = created
        job.running_at = created
        job.ended_at = ended

        # write the content into result.json
        #_log_result(_make_resultjson(job, req_json), "result.json")
        _log_result(_make_resultjson(response, req_json), "result.json")

        # raise error if the job execution is failed
        if response.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {response.message or _job_message(job)}"  # noqa: E501
            raise SseRuntimeError(msg)
        if job.status != "succeeded":
            msg = f"To execute sampling on OQTOPUS server is failed. reason: {_job_message(job)}"  # noqa: E501
            raise SseRuntimeError(msg)

        return job


def _make_client_job(
    response: sse_pb2.SseEngineResponse,
    request_job: dict[str, Any],
) -> JobsJob:
    result_dict = _load_json_dict(response.job_json)
    print("###############")
    print(result_dict)
    #result_dict["job_info"] = _make_client_job_info(result_dict, request_job, response.message)
    result_dict["job_info"] = {
        "input": {
            "program": (request_job.get("upload_info") or {}).get("program") or [],
            "operator": (request_job.get("upload_info") or {}).get("operator") or [],
            #"sse_program": (request_job.get("upload_info") or {}).get("sse_program"),
            "sse_program": ""
        },
        "program": request_job.get("program") or [],
        "combined_program": result_dict.get("combined_program"),
        "operator": request_job.get("operator") or [],
        "result": result_dict.get("result"),
        "transpile_result": result_dict.get("transpile_result"),
        "sse_log": result_dict.get("sse_log"),
        "message": result_dict.get("message") or response.message,
    }
    print("###############")
    print(result_dict)
    job = JobsJob.from_dict(result_dict)
    print("###############")
    print(job)
    if job is None:
        msg = "Could not parse job data"
        raise SseRuntimeError(msg)
    return job
    # # from oqtopus_client import OqtopusClient
    # # cli = OqtopusClient()
    # # r = cli._to_result(job)
    # # print(r)
    # # return r

    # payload = {
    #     "job_id": result_dict.get("job_id") or request_job.get("job_id") or "",
    #     "name": result_dict.get("name") or request_job.get("name") or "",
    #     "description": result_dict.get("description") or request_job.get("description"),
    #     "job_type": (
    #         result_dict.get("job_type")
    #         or request_job.get("job_type")
    #         or "sampling"
    #     ),
    #     "status": result_dict.get("status") or response.status or "failed",
    #     "device_id": result_dict.get("device_id") or request_job.get("device_id") or "",
    #     "shots": result_dict.get("shots") or request_job.get("shots") or 1,
    #     "job_info": _make_client_job_info(result_dict, request_job, response.message),
    #     "transpiler_info": (
    #         result_dict.get("transpiler_info")
    #         or request_job.get("transpiler_info")
    #         or {}
    #     ),
    #     "simulator_info": (
    #         result_dict.get("simulator_info")
    #         or request_job.get("simulator_info")
    #         or {}
    #     ),
    #     "mitigation_info": (
    #         result_dict.get("mitigation_info")
    #         or request_job.get("mitigation_info")
    #         or {}
    #     ),
    #     "execution_time": result_dict.get("execution_time"),
    #     "submitted_at": result_dict.get("submitted_at"),
    #     "ready_at": result_dict.get("ready_at"),
    #     "running_at": result_dict.get("running_at"),
    #     "ended_at": result_dict.get("ended_at"),
    # }

    # # Filter out unsupported fields to ensure compatibility with JobsJobDef
    # for key in ["parent", "children"]:
    #     payload.pop(key, None)

    # job = JobsJobDef.from_dict(payload)
    # if job is None:
    #     msg = "Could not parse job data"
    #     raise SseRuntimeError(msg)
    # return job


def _make_resultjson(response: sse_pb2.SseEngineResponse, request_job: dict[str, Any]) -> dict[str, Any]:
    result_dict = _load_json_dict(response.job_json)
    # result_dict["input"] = request_job.get("input") or ""
    # result_dict["program"] = request_job.get("program") or []
    # result_dict["operator"] = request_job.get("operator") or []
    # result_dict["sse_program"] = request_job.get("sse_program") #TODO: necessary?
    return result_dict

    # job_info = job.job_info.to_dict() if job.job_info is not None else {}
    # return {
    #     "job_id": job.job_id,
    #     "name": job.name,
    #     "description": job.description,
    #     "device_id": job.device_id,
    #     "shots": job.shots,
    #     "job_type": _enum_value(job.job_type),
    #     "input": request_job.get("input") or "",
    #     "program": request_job.get("program") or [],
    #     "operator": request_job.get("operator") or [],
    #     "sse_program": request_job.get("sse_program"),
    #     "combined_program": job_info.get("combined_program"),
    #     "transpile_result": job_info.get("transpile_result"),
    #     "result": job_info.get("result"),
    #     "sse_log": job_info.get("sse_log"),
    #     "output_files": request_job.get("output_files") or [],
    #     "transpiler_info": (
    #         job.transpiler_info or request_job.get("transpiler_info") or {}
    #     ),
    #     "simulator_info": job.simulator_info or request_job.get("simulator_info") or {},
    #     "mitigation_info": (
    #         job.mitigation_info or request_job.get("mitigation_info") or {}
    #     ),
    #     "status": _enum_value(job.status),
    #     "message": _job_message(job),
    #     "execution_time": job.execution_time,
    #     "submitted_at": job.submitted_at,
    #     "ready_at": job.ready_at,
    #     "running_at": job.running_at,
    #     "ended_at": job.ended_at,
    # }


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

    return {"submit_job_request": request,  "upload_info": upload_info.to_dict()}

    # return {
    #     "job_id": job_dict.get("job_id") or "",
    #     "name": job_dict.get("name") or "",
    #     "description": job_dict.get("description"),
    #     "device_id": job_dict.get("device_id") or "",
    #     "shots": shots,
    #     "job_type": "sampling",
    #     "input": job_dict.get("input") or "",
    #     "program": program,
    #     "operator": job_dict.get("operator") or [],
    #     "sse_program": job_dict.get("sse_program"),
    #     "combined_program": job_dict.get("combined_program"),
    #     "transpile_result": None,
    #     "result": None,
    #     "sse_log": None,
    #     "output_files": job_dict.get("output_files") or [],
    #     "transpiler_info": transpiler_info or {},
    #     "simulator_info": job_dict.get("simulator_info") or {},
    #     "mitigation_info": job_dict.get("mitigation_info") or {},
    #     "status": "submitted",
    #     "message": None,
    # }


def _load_json_dict(raw_json: str | None) -> dict[str, Any]:
    if not raw_json:
        return {}

    loaded = json.loads(raw_json)
    if not isinstance(loaded, dict):
        msg = "Expected job data to be a JSON object"
        raise SseRuntimeError(msg)
    return loaded


# def _make_client_job_info(
#     result_dict: dict[str, Any],
#     request_job: dict[str, Any],
#     response_message: str,
# ) -> dict[str, Any]:
#     return {
#         "input": {
#             "program": request_job.get("program") or [],
#             "operator": request_job.get("operator") or [],
#             #"sse_program": request_job.get("sse_program"),
#             "sse_program": ""
#         },
#         "program": request_job.get("program") or [],
#         "combined_program": result_dict.get("combined_program"),
#         "operator": request_job.get("operator") or [],
#         "result": result_dict.get("result"),
#         "transpile_result": result_dict.get("transpile_result"),
#         "sse_log": result_dict.get("sse_log"),
#         "message": result_dict.get("message") or response_message,
#     }


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


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
