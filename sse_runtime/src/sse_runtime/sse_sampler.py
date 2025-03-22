import datetime
import json
import os
from pathlib import Path
from typing import Any

import grpc
from quri_parts.backend import BackendError
from quri_parts_oqtopus.rest.models.jobs_job_def import JobsJobDef
from quri_parts_oqtopus.rest.models.jobs_job_info import JobsJobInfo
from quri_parts_oqtopus.rest.models.jobs_sampling_result import JobsSamplingResult
from quri_parts_oqtopus.rest.models.jobs_transpile_result import JobsTranspileResult
from sse_interface.v1 import sse_pb2, sse_pb2_grpc


def req_transpile_and_exec(
        qasm: list[str],
        n_shots: int,
        transpiler: dict[str, Any]
) -> JobsJobDef:
    """Request transpile and QPU execution.

    Args:
        qasm: The QASM string to be transpiled and executed.
        n_shots: The number of shots.
        transpiler: Transpiler info to pass to transpiler.

    Returns:
        JobsJobDef | None: If a timeout occurs, it returns None. Otherwise, it
            returns the Job.

    Raises:
        OSError: If the job data is not set.
        BackendError: If the job execution fails.

    """
    # get gRPC server address and port from environment variables
    grpc_sse_qmt_router_host = os.environ.get("GRPC_SSE_QMT_ROUTER_HOST",
                                              "sse.qmt.router")
    grpc_sse_qmt_router_port = os.environ.get("GRPC_SSE_QMT_ROUTER_PORT",
                                              "5001")

    # get job data from environment variable
    job_json = os.environ.get("JOB_DATA_JSON")
    if not job_json:
        msg = "Could not get job data"
        raise OSError(msg)

    with grpc.insecure_channel(
        f"{grpc_sse_qmt_router_host}:{grpc_sse_qmt_router_port}"
    ) as channel:
        created = datetime.datetime.now(tz=datetime.UTC) \
                                    .strftime("%Y-%m-%d %H:%M:%S")
        stub = sse_pb2_grpc.SSEServiceStub(channel)
        # insert parameter of qasm, shots and transpiler into the job data
        req_json = _make_request(job_json, qasm[0], n_shots, transpiler)
        # gRPC request
        request = sse_pb2.TranspileAndExecRequest(job_data_json=json.dumps(req_json))
        response = stub.TranspileAndExec(request)

        # make content of output file to pass the result to sserunner
        ended = datetime.datetime.now(tz=datetime.UTC) \
                        .strftime("%Y-%m-%d %H:%M:%S")
        job = _make_job_def(job_json, qasm[0], n_shots, transpiler, response)
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

        return job


def _make_job_def(
    job_json: str,
    qasm: str,
    n_shots: int,
    transpiler: dict[str, Any],
    response: sse_pb2.TranspileAndExecResponse,
) -> JobsJobDef:
    job_dict = json.loads(job_json)
    result_dict = json.loads(response.result or "{}")

    # result related data
    counts = result_dict.get("counts", None)

    # transpile result related data
    transpiled_program = response.transpiled_qasm or ""
    stats = result_dict.get("transpiler_info", {}).get("stats", "")
    virtual_physical_mapping = json.dumps(
        result_dict.get("transpiler_info", {}).get("virtual_physical_mapping", {})
    )

    # job_info related data
    program = [qasm]
    message = response.message or ""

    # make job objects
    sampling_result = JobsSamplingResult(
        counts=counts,
    )
    transpile_result = JobsTranspileResult(
        transpiled_program=transpiled_program,
        stats=stats,
        virtual_physical_mapping=virtual_physical_mapping,
    )
    job_info = JobsJobInfo(
        program=program,
        result={
            "sampling": sampling_result,
            "estimation": None,
        },
        transpile_result=transpile_result,
        message=message,
    )
    transpiler_ = transpiler
    if response.transpiler_info:
        transpiler_ = json.loads(response.transpiler_info)

    return JobsJobDef(
        job_id=job_dict.get("ID"),
        job_type=job_dict.get("JobType"),
        description="",  # unable to get from job data sent from engine
        status=response.status,
        name="",  # unable to get from job data sent from engine
        device_id="",  # unable to get from job data sent from engine
        transpiler_info=transpiler_,
        shots=n_shots,
        job_info=job_info,
    )


def _make_resultjson(job: JobsJobDef) -> dict[str, Any]:
    # convert the job data in order to make it readable in engine

    output_contents = job.to_dict()
    transpile_result_dict = output_contents["job_info"]["transpile_result"]
    # convert the string to dictionary
    transpile_result_dict["virtual_physical_mapping"] = json.loads(
        transpile_result_dict["virtual_physical_mapping"]
    )
    return output_contents


def _make_request(
        job_json: str,
        qasm: str,
        shots: int,
        transpiler_info: dict[str, Any]
) -> str:
    job_dict = json.loads(job_json)
    return {
        "id": job_dict["ID"],
        "qasm": qasm,
        "shots": shots,
        "transpiler_info": transpiler_info or {},
    }


def _log_result(contents_dict: dict[str, Any], filename: str) -> None:
    # write the result to a file
    dir_path = os.environ.get("OUT_PATH", "")
    p = Path(dir_path)
    if not p.exists:
        msg = f"The path to output does not exist {dir_path}"
        raise BackendError(msg)

    file_path = Path(dir_path, filename)
    with Path.open(file_path, "w") as f:
        json.dump(contents_dict, f, indent=2)
