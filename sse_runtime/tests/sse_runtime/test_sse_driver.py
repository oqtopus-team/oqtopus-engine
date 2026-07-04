# ruff: noqa: E402, SLF001, PLR0913, PLR0917

import datetime
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

src_root = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(src_root))
sys.path.append(str(src_root / "sse_runtime"))

# Load core Job model directly without triggering the package's full __init__
# (opentelemetry etc. are not installed in this environment).
_core_model_path = (
    Path(__file__).resolve().parents[3]
    / "core"
    / "src"
    / "oqtopus_engine_core"
    / "framework"
    / "model.py"
)
_spec = importlib.util.spec_from_file_location("_core_model", _core_model_path)
_core_model = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_core_model)  # type: ignore[union-attr]
_Job = _core_model.Job
_OperatorItem = _core_model.OperatorItem
_TranspileResult = _core_model.TranspileResult
_JobResult = _core_model.JobResult
_SamplingResult = _core_model.SamplingResult
_EstimationResult = _core_model.EstimationResult

import sse_driver
from oqtopus_client.rest.models.jobs_job import JobsJob
from oqtopus_client.rest.models.jobs_job_info import JobsJobInfo
from oqtopus_client.rest.models.jobs_job_type import JobsJobType
from oqtopus_client.rest.models.jobs_s3_operator_item import JobsS3OperatorItem
from oqtopus_client.rest.models.jobs_s3_submit_job_info import JobsS3SubmitJobInfo
from oqtopus_client.rest.models.jobs_submit_job_request import JobsSubmitJobRequest


class _DummyChannel:
    def __init__(self, value: object) -> None:
        self._value = value

    def __enter__(self) -> object:
        return self._value

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


def _build_submit_job_request() -> JobsSubmitJobRequest:
    return JobsSubmitJobRequest(
        name="sse-job",
        device_id="device-a",
        job_type=JobsJobType.SSE,
        shots=1024,
    )


def _build_upload_info() -> JobsS3SubmitJobInfo:
    return JobsS3SubmitJobInfo(
        program=["OPENQASM 3;"],
        sse_program="print('hello')",
    )


def _build_submit_job_request_full() -> JobsSubmitJobRequest:
    return JobsSubmitJobRequest(
        name="sse-job",
        description="full-request",
        device_id="device-a",
        job_type=JobsJobType.SSE,
        transpiler_info={"level": 2},
        simulator_info={"backend": "statevector"},
        mitigation_info={"method": "zne"},
        shots=2048,
    )


def _build_upload_info_with_operator() -> JobsS3SubmitJobInfo:
    return JobsS3SubmitJobInfo(
        program=["OPENQASM 3;"],
        operator=[JobsS3OperatorItem(pauli="Z0", coeff=1.0)],
        sse_program="print('hello')",
    )


def _to_gateway_response_json(job: Any) -> str:
    """Build the exact response payload shape used by SseEngineGateway.

    core/src/oqtopus_engine_core/fetchers/sse_engine_gateway.py sets:
    `res.job_json = job.model_dump_json(exclude={"parent", "children"})`
    This helper intentionally mirrors that serialization path.

    Returns:
        str: Gateway-equivalent JSON payload as a string.
    """
    return job.model_dump_json(exclude={"parent", "children"})


def _build_minimal_response_job_json(
    *,
    job_id: str = "parent-1",
    status: str,
    shots: int = 1024,
    message: str,
    input_path: str = "s3://bucket/input.json",
    include_operator: bool = False,
) -> str:
    return _to_gateway_response_json(
        _Job(
            job_id=job_id,
            name="sse-result",
            job_type="sse",
            status=status,
            device_id="device-a",
            shots=shots,
            input=input_path,
            program=["OPENQASM 3;"],
            operator=[_OperatorItem(pauli="Z0", coeff=1.0)]
            if include_operator
            else None,
            sse_program="print('hello')",
            transpiler_info={},
            simulator_info={},
            mitigation_info={},
            message=message,
        )
    )


def _build_response_job_full(*, status: str, message: str | None = None) -> Any:
    return _Job(
        job_id="parent-2",
        name="sse-result-full",
        description="full response payload",
        job_type="sse",
        status=status,
        device_id="device-b",
        shots=2048,
        input="s3://bucket/input.json",
        program=["OPENQASM 3;", 'include "stdgates.inc";'],
        operator=[_OperatorItem(pauli="Z0", coeff=1.0)],
        sse_program="print('hello from sse')",
        combined_program="s3://bucket/combined_program.zip",
        transpile_result=_TranspileResult(
            transpiled_program="OPENQASM 3;",
            stats={"depth": 12},
            virtual_physical_mapping={"0": 0},
        ),
        result=_JobResult(
            sampling=_SamplingResult(counts={"00": 10}, divided_counts={"00": 1.0}),
            estimation=_EstimationResult(exp_value=0.5, stds=0.1),
        ),
        sse_log="s3://bucket/sse.log",
        output_files=[
            "s3://bucket/combined_program.zip",
            "s3://bucket/result.zip",
            "s3://bucket/transpile_result.zip",
            "s3://bucket/sse.log",
        ],
        transpiler_info={"level": 3},
        simulator_info={"backend": "density_matrix"},
        mitigation_info={"method": "pec"},
        execution_time=12.5,
        message=message,
        submitted_at=datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
        ready_at=datetime.datetime(2026, 1, 2, 3, 4, 6, tzinfo=datetime.UTC),
        running_at=datetime.datetime(2026, 1, 2, 3, 4, 7, tzinfo=datetime.UTC),
        ended_at=datetime.datetime(2026, 1, 2, 3, 4, 8, tzinfo=datetime.UTC),
    )


def _build_response_job_json_full(*, status: str, message: str) -> str:
    job = _build_response_job_full(status=status, message=message)
    return _to_gateway_response_json(job)


def _assert_common_job_fields(
    job: JobsJob,
    *,
    job_id: str,
    name: str,
    device_id: str,
    shots: int,
    status: str,
) -> None:
    assert isinstance(job, JobsJob)
    assert job.job_id == job_id
    assert job.name == name
    assert job.device_id == device_id
    assert job.shots == shots
    assert str(job.status) == status


def _assert_job_info_message_and_input(
    job: JobsJob,
    *,
    message: str,
    input_path: str,
) -> None:
    assert job.job_info is not None
    assert isinstance(job.job_info, JobsJobInfo)
    assert job.job_info.message == message
    assert job.job_info.input == input_path


def _assert_minimal_job_defaults(job: JobsJob) -> None:
    assert str(job.job_type) == "JobsJobType.SSE"
    assert job.transpiler_info == {}
    assert job.simulator_info == {}
    assert job.mitigation_info == {}
    assert job.execution_time is None
    assert job.submitted_at is None
    assert job.ready_at is None
    assert job.running_at is None
    assert job.ended_at is None
    assert job.job_info is not None
    assert job.job_info.combined_program is None
    assert job.job_info.result is None
    assert job.job_info.transpile_result is None
    assert job.job_info.sse_log is None


def _assert_full_job_fields(job: JobsJob) -> None:
    assert isinstance(job, JobsJob)
    assert job.job_id == "parent-2"
    assert job.name == "sse-result-full"
    assert job.description == "full response payload"
    assert str(job.job_type) == "JobsJobType.SSE"
    assert str(job.status) == "JobsJobStatus.SUCCEEDED"
    assert job.device_id == "device-b"
    assert job.shots == 2048
    assert job.transpiler_info == {"level": 3}
    assert job.simulator_info == {"backend": "density_matrix"}
    assert job.mitigation_info == {"method": "pec"}
    assert job.execution_time == pytest.approx(12.5)
    assert job.submitted_at == datetime.datetime(
        2026,
        1,
        2,
        3,
        4,
        5,
        tzinfo=datetime.UTC,
    )
    assert job.ready_at == datetime.datetime(2026, 1, 2, 3, 4, 6, tzinfo=datetime.UTC)
    assert job.running_at == datetime.datetime(2026, 1, 2, 3, 4, 7, tzinfo=datetime.UTC)
    assert job.ended_at == datetime.datetime(2026, 1, 2, 3, 4, 8, tzinfo=datetime.UTC)


def _assert_full_job_info_fields(job: JobsJob, *, message: str) -> None:
    assert job.job_info is not None
    assert isinstance(job.job_info, JobsJobInfo)
    assert job.job_info.input == "s3://bucket/input.json"
    assert job.job_info.combined_program == "s3://bucket/combined_program.zip"
    assert job.job_info.result == {
        "sampling": {"counts": {"00": 10}, "divided_counts": {"00": 1.0}},
        "estimation": {"exp_value": 0.5, "stds": 0.1},
    }
    assert job.job_info.transpile_result == {
        "transpiled_program": "OPENQASM 3;",
        "stats": {"depth": 12},
        "virtual_physical_mapping": {"0": 0},
    }
    assert job.job_info.sse_log == "s3://bucket/sse.log"
    assert job.job_info.message == message


def _assert_make_request_payload(
    request: dict[str, Any],
    *,
    expected_request: dict[str, Any],
) -> None:
    assert request == expected_request


def _build_expected_request(
    input_job: JobsSubmitJobRequest,
    upload_info: JobsS3SubmitJobInfo,
    *,
    parent_job_id: str,
) -> dict[str, Any]:
    expected_request = input_job.model_dump()
    expected_upload_info = upload_info.model_dump()
    expected_request["job_id"] = parent_job_id
    expected_request["status"] = "ready"
    expected_request["name"] = expected_request["name"] or ""
    expected_request["program"] = expected_upload_info.get("program") or []
    expected_request["operator"] = expected_upload_info.get("operator") or []
    expected_request["input"] = ""
    expected_request["transpiler_info"] = expected_request.get("transpiler_info") or {}
    expected_request["simulator_info"] = expected_request.get("simulator_info") or {}
    expected_request["mitigation_info"] = expected_request.get("mitigation_info") or {}
    return expected_request


# -----------------------------
# JSON/serialization utilities
# -----------------------------
@pytest.mark.parametrize("raw_json", [None, ""])
def test_load_json_dict_returns_empty_when_raw_json_is_missing_or_empty(
    raw_json: str | None,
) -> None:
    assert sse_driver._load_json_dict(raw_json) == {}


def test_load_json_dict_raises_when_json_is_not_object() -> None:
    with pytest.raises(sse_driver.SseRuntimeError, match="JSON object"):
        sse_driver._load_json_dict("[]")


# -----------------
# Request builders
# -----------------
@pytest.mark.parametrize(
    ("input_job_factory", "job_json"),
    [
        pytest.param(
            _build_submit_job_request,
            '{"job_id": "parent-id"}',
            id="overrides-fields",
        ),
        pytest.param(
            _build_submit_job_request_full,
            '{"job_id": "parent-id"}',
            id="keeps-model-fields",
        ),
    ],
)
def test_make_request_merges_submit_job_request_and_upload_info(
    input_job_factory: Any,
    job_json: str,
) -> None:
    input_job = input_job_factory()
    upload_info = _build_upload_info()

    request = sse_driver._make_request(job_json, input_job, upload_info)

    _assert_make_request_payload(
        request,
        expected_request=_build_expected_request(
            input_job,
            upload_info,
            parent_job_id="parent-id",
        ),
    )


def test_make_request_serializes_operator_items() -> None:
    request = sse_driver._make_request(
        '{"job_id": "parent-id"}',
        _build_submit_job_request(),
        _build_upload_info_with_operator(),
    )

    assert request["operator"] == [{"pauli": "Z0", "coeff": 1.0}]


# -----------------
# Result file I/O
# -----------------
def test_log_result_writes_json_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OUT_PATH", str(tmp_path))

    payload = {"status": "succeeded", "count": 3}
    sse_driver._log_result(json.dumps(payload), "result.json")

    output_file = tmp_path / "result.json"
    assert output_file.exists()
    assert json.loads(output_file.read_text()) == payload


def test_log_result_raises_when_out_path_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OUT_PATH", "/path/does/not/exist")

    with pytest.raises(sse_driver.SseRuntimeError, match="does not exist"):
        sse_driver._log_result(json.dumps({"status": "failed"}), "result.json")


def test_submit_job_raises_when_job_json_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOB_JSON", raising=False)

    with pytest.raises(OSError, match="Could not get job data"):
        sse_driver.submit_job(
            _build_submit_job_request(),
            _build_upload_info(),
        )


# -----------------------------
# Gateway response conversion
# -----------------------------
@pytest.mark.parametrize(
    ("job_id", "status", "shots", "message", "expected_status"),
    [
        ("job-1", "succeeded", 1024, "ok", "JobsJobStatus.SUCCEEDED"),
        ("job-2", "failed", 1, "backend failed", "JobsJobStatus.FAILED"),
    ],
)
def test_convert_to_oqtopus_client_job_model_maps_minimal_payload(
    job_id: str,
    status: str,
    shots: int,
    message: str,
    expected_status: str,
) -> None:
    result_json = _build_minimal_response_job_json(
        job_id=job_id,
        status=status,
        shots=shots,
        message=message,
        input_path="s3://bucket/input.json",
        include_operator=status == "failed",
    )

    converted = sse_driver._convert_to_oqtopus_client_job_model(result_json)

    _assert_common_job_fields(
        converted,
        job_id=job_id,
        name="sse-result",
        device_id="device-a",
        shots=shots,
        status=expected_status,
    )
    _assert_job_info_message_and_input(
        converted,
        message=message,
        input_path="s3://bucket/input.json",
    )
    _assert_minimal_job_defaults(converted)


def test_convert_to_oqtopus_client_job_model_raises_when_job_parse_fails() -> None:
    with pytest.raises(sse_driver.SseRuntimeError, match="Could not parse job data"):
        sse_driver._convert_to_oqtopus_client_job_model(json.dumps({"job_id": "job-1"}))


def test_convert_to_oqtopus_client_job_model_uses_flat_gateway_payload_fields() -> None:
    result_json = _build_minimal_response_job_json(
        job_id="job-3",
        status="failed",
        shots=1,
        message="top-level message",
        input_path="s3://bucket/top-level-input.json",
        include_operator=True,
    )

    converted = sse_driver._convert_to_oqtopus_client_job_model(result_json)

    _assert_job_info_message_and_input(
        converted,
        message="top-level message",
        input_path="s3://bucket/top-level-input.json",
    )


def test_convert_to_oqtopus_client_job_model_all_fields_from_model_payload() -> None:
    result_json = _build_response_job_json_full(
        status="succeeded", message="all fields present"
    )

    converted = sse_driver._convert_to_oqtopus_client_job_model(result_json)

    _assert_full_job_fields(converted)


def test_convert_to_oqtopus_client_job_model_all_job_info_fields() -> None:
    result_json = _build_response_job_json_full(
        status="failed", message="full info fields"
    )

    converted = sse_driver._convert_to_oqtopus_client_job_model(result_json)

    _assert_full_job_info_fields(converted, message="full info fields")


def test_convert_to_oqtopus_client_job_model_ignores_nested_job_info_key() -> None:
    # _convert_to_oqtopus_client_job_model always builds job_info from the flat
    # result_dict (i.e. Job.model_dump_json output).  A "job_info" sub-key is
    # not used; flat fields take precedence regardless.
    result_json = _to_gateway_response_json(
        _Job(
            job_id="job-4",
            name="flat-wins",
            job_type="sse",
            status="failed",
            device_id="device-a",
            shots=1,
            input="s3://bucket/flat-input.json",
            transpiler_info={},
            simulator_info={},
            mitigation_info={},
            message="flat message",
        )
    )
    # Inject an extraneous nested job_info key (should be ignored).
    result_dict = json.loads(result_json)
    result_dict["job_info"] = {
        "input": "s3://bucket/nested-input.json",
        "message": "nested message",
    }

    converted = sse_driver._convert_to_oqtopus_client_job_model(json.dumps(result_dict))

    # Flat fields from the model are used; the nested job_info key is ignored.
    _assert_common_job_fields(
        converted,
        job_id="job-4",
        name="flat-wins",
        device_id="device-a",
        shots=1,
        status="JobsJobStatus.FAILED",
    )
    _assert_job_info_message_and_input(
        converted,
        message="flat message",
        input_path="s3://bucket/flat-input.json",
    )


# ----------------
# gRPC submit flow
# ----------------
def test_submit_job_grpc_success_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JOB_JSON", '{"job_id":"parent-1"}')
    monkeypatch.setenv("SSE_ENGINE_ADDRESS", "test-host:1234")
    monkeypatch.setenv("OUT_PATH", str(tmp_path))

    observed: dict[str, object] = {}
    channel_value = object()

    def _fake_insecure_channel(
        address: str,
        options: list[tuple[str, int]] | None = None,
    ) -> _DummyChannel:
        observed["address"] = address
        observed["options"] = options
        return _DummyChannel(channel_value)

    fake_response = SimpleNamespace(
        status="succeeded",
        message="",
        job_json=_build_minimal_response_job_json(status="succeeded", message="ok"),
    )

    class _FakeStub:
        def SseEngine(self, request: Any) -> object:  # noqa: N802
            observed["request_job_json"] = request.job_json
            return fake_response

    monkeypatch.setattr(sse_driver.grpc, "insecure_channel", _fake_insecure_channel)
    monkeypatch.setattr(
        sse_driver.sse_pb2_grpc,
        "SseEngineServiceStub",
        lambda _channel: _FakeStub(),
    )

    job = sse_driver.submit_job(
        _build_submit_job_request(),
        _build_upload_info(),
    )

    _assert_common_job_fields(
        job,
        job_id="parent-1",
        name="sse-result",
        device_id="device-a",
        shots=1024,
        status="JobsJobStatus.SUCCEEDED",
    )
    _assert_job_info_message_and_input(
        job,
        message="ok",
        input_path="s3://bucket/input.json",
    )
    assert observed["address"] == "test-host:1234"
    assert observed["options"] == [
        ("grpc.max_receive_message_length", 4 * 1024 * 1024),
        ("grpc.max_send_message_length", 4 * 1024 * 1024),
    ]
    assert '"job_id": "parent-1"' in str(observed["request_job_json"])
    assert '"status": "ready"' in str(observed["request_job_json"])
    assert job.submitted_at is not None
    assert job.ready_at is not None
    assert job.running_at is not None
    assert job.ended_at is not None


@pytest.mark.parametrize(
    ("grpc_status", "grpc_message", "job_status", "job_message", "expected_error"),
    [
        ("failed", "grpc failed", "succeeded", "job success", "grpc failed"),
        ("succeeded", "", "failed", "job failed", "job failed"),
    ],
)
def test_submit_job_raises_when_status_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    grpc_status: str,
    grpc_message: str,
    job_status: str,
    job_message: str,
    expected_error: str,
) -> None:
    monkeypatch.setenv("JOB_JSON", '{"job_id":"parent-1"}')
    monkeypatch.setenv("OUT_PATH", str(tmp_path))

    fake_response = SimpleNamespace(
        status=grpc_status,
        message=grpc_message,
        job_json=_build_minimal_response_job_json(
            status=job_status, message=job_message
        ),
    )

    class _FakeStub:
        def SseEngine(self, _request: object) -> object:  # noqa: N802
            return fake_response

    monkeypatch.setattr(
        sse_driver.grpc,
        "insecure_channel",
        lambda _address, options=None: _DummyChannel(object()),
    )
    monkeypatch.setattr(
        sse_driver.sse_pb2_grpc,
        "SseEngineServiceStub",
        lambda _channel: _FakeStub(),
    )
    with pytest.raises(sse_driver.SseRuntimeError, match=expected_error):
        sse_driver.submit_job(
            _build_submit_job_request(),
            _build_upload_info(),
        )
