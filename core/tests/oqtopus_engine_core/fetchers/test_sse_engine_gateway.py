from types import SimpleNamespace

import pytest

from oqtopus_engine_core.fetchers.sse_engine_gateway import SseEngineGatewayServicer
from oqtopus_engine_core.framework.model import Job


def _job_json(*, job_id: str = "parent-1-sse-0", **overrides: object) -> str:
    job = Job(
        job_id=job_id,
        job_type="sampling",
        device_id="test-device",
        shots=1024,
        input="test-input",
        program=["OPENQASM 3;"],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
        **overrides,
    )
    return job.model_dump_json(exclude={"parent", "children"})


def test_get_job_from_request_sets_repository_job_id_to_job_id() -> None:
    """The internal job is always self-referential (see
    docs/design/pipeline_execution.md, Job ID Conventions): sse_driver.py
    numbers every internal call with its own engine-unique job_id, so this
    is safe.
    """
    request = SimpleNamespace(job_json=_job_json(job_id="parent-1-sse-0"))

    job = SseEngineGatewayServicer._get_job_from_request(request)

    assert job.job_id == "parent-1-sse-0"
    assert job.repository_job_id == "parent-1-sse-0"


def test_get_job_from_request_overrides_any_repository_job_id_in_payload() -> None:
    """Never trust a repository_job_id carried in the incoming payload."""
    request = SimpleNamespace(
        job_json=_job_json(job_id="parent-1-sse-0", repository_job_id="unrelated-id")
    )

    job = SseEngineGatewayServicer._get_job_from_request(request)

    assert job.repository_job_id == "parent-1-sse-0"


def test_get_job_from_request_raises_when_job_json_empty() -> None:
    request = SimpleNamespace(job_json="")

    with pytest.raises(ValueError, match="job json in the request data is empty"):
        SseEngineGatewayServicer._get_job_from_request(request)


def test_get_job_from_request_raises_when_job_json_invalid() -> None:
    request = SimpleNamespace(job_json="not valid json")

    with pytest.raises(ValueError, match="failed to convert JSON to a Job object"):
        SseEngineGatewayServicer._get_job_from_request(request)
