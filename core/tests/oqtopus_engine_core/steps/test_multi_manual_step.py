import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from oqtopus_engine_core.framework import (
    Device,
    GlobalContext,
    Job,
    JobResult,
)
from oqtopus_engine_core.framework.job_repository import JobRepository
from oqtopus_engine_core.steps.multi_manual_step import (
    COMBINED_QUBITS_LIST_KEY,
    MultiManualStep,
    divide_result,
    divide_string_by_lengths,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEVICE_INFO_JSON = json.dumps({"qubits": [0, 1, 2, 3, 4]})


def _make_device(**overrides: Any) -> Device:
    defaults: dict[str, Any] = {
        "device_id": "test-device",
        "device_type": "QPU",
        "status": "available",
        "n_qubits": 5,
        "basis_gates": ["x", "h", "cx"],
        "instructions": [],
        "device_info": _DEVICE_INFO_JSON,
        "description": "test device",
    }
    return Device(**{**defaults, **overrides})


def _make_gctx(**overrides: Any) -> GlobalContext:
    defaults: dict[str, Any] = {
        "config": {},
        "device": _make_device(),
        "job_repository": AsyncMock(spec=JobRepository),
    }
    return GlobalContext(**{**defaults, **overrides})


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "job_id": "test-job-001",
        "device_id": "test-device",
        "job_type": "multi_manual",
        "shots": 1024,
        "status": "ready",
        "transpiler_info": {},
        "simulator_info": {},
        "mitigation_info": {},
        "job_info": {
            "program": ["OPENQASM 3; h q[0];", "OPENQASM 3; cx q[0],q[1];"],
            "transpile_result": None,
            "message": None,
            "result": None,
            "operator": [],
        },
    }
    data: dict[str, Any] = {**defaults, **overrides}
    if "job_info" in overrides and isinstance(overrides["job_info"], dict):
        data["job_info"] = {**defaults["job_info"], **overrides["job_info"]}
    return Job(**data)


def _make_combine_response(
    status: int = 0,
    combined_program: str = "combined_qasm",
    combined_qubits_list: list[int] | None = None,
) -> MagicMock:
    resp: MagicMock = MagicMock()
    resp.combined_status = status
    resp.combined_program = combined_program
    resp.combined_qubits_list = combined_qubits_list or [1, 3]
    return resp


# ---------------------------------------------------------------------------
# divide_string_by_lengths tests
# ---------------------------------------------------------------------------


class TestDivideStringByLengths:
    @pytest.mark.parametrize(
        ("input_str", "lengths", "expected", "error"),
        [
            ("", [], [], None),
            ("", [0, 0], ["", ""], None),
            ("01", [2], ["01"], None),
            ("011000101", [2, 3, 4], ["01", "100", "0101"], None),
            ("011000101", [2, 3, 0, 4, 0], ["01", "100", "", "0101", ""], None),
            ("0110001011", [2, 3, 4], None, "inconsistent qubits"),
            ("01100010", [2, 3, 4], None, "inconsistent qubits"),
            ("011000101", [2, 3, 4, 1], None, "inconsistent qubits"),
            ("011000101", [2, 3], None, "inconsistent qubits"),
            ("011000101", [], None, "inconsistent qubits"),
            ("", [1, 2], None, "inconsistent qubits"),
            # non-integer in lengths triggers except branch
            ("abcdef", [2, "bad", 1], None, "inconsistent qubits"),
        ],
    )
    def test_divide_string_by_lengths(
        self,
        input_str: str,
        lengths: list[Any],
        expected: list[str] | None,
        error: str | None,
    ) -> None:
        if error:
            with pytest.raises(ValueError, match=error):
                divide_string_by_lengths(input_str, lengths)
        else:
            assert divide_string_by_lengths(input_str, lengths) == expected


# ---------------------------------------------------------------------------
# divide_result tests
# ---------------------------------------------------------------------------


class TestDivideResult:
    @pytest.mark.parametrize(
        ("counts", "combined_qubits_list", "expected_divided", "error"),
        [
            # 1 circuit
            (
                {
                    "0001": 1,
                    "0100": 2,
                    "1000": 4,
                    "1111": 8,
                    "0010": 16,
                    "0110": 32,
                    "1011": 64,
                },
                [4],
                {
                    0: {
                        "0001": 1,
                        "0100": 2,
                        "1000": 4,
                        "0010": 16,
                        "0110": 32,
                        "1011": 64,
                        "1111": 8,
                    }
                },
                None,
            ),
            # 2 circuits
            (
                {
                    "0001": 1,
                    "0100": 2,
                    "1000": 4,
                    "1111": 8,
                    "0010": 16,
                    "0110": 32,
                    "1011": 64,
                },
                [1, 3],
                {
                    0: {"0": 54, "1": 73},
                    1: {
                        "000": 1,
                        "010": 2,
                        "100": 4,
                        "001": 16,
                        "011": 32,
                        "101": 64,
                        "111": 8,
                    },
                },
                None,
            ),
            # Negative test - no qubit
            ({}, [], None, "inconsistent qubit property"),
            # Key length doesn't match combined_qubits_list
            ({"AB": 1}, [2, 3], None, "inconsistent qubits"),
        ],
    )
    def test_divide_result(
        self,
        counts: dict[str, int],
        combined_qubits_list: list[int],
        expected_divided: dict[int, dict[str, int]] | None,
        error: str | None,
    ) -> None:
        job_result = JobResult(
            sampling={
                "counts": counts,
                "divided_counts": None,
            },
            estimation=None,
        )
        job = _make_job(
            job_info={
                "program": ["test_program"],
                "combined_program": "test_combined_program",
                "result": job_result,
                "transpile_result": None,
                "message": None,
                "operator": [],
            },
            status="COMPLETED",
        )
        if error:
            with pytest.raises(ValueError, match=error):
                divide_result(
                    job=job,
                    combined_qubits_list=combined_qubits_list,
                )
        else:
            divided_counts = divide_result(
                job=job,
                combined_qubits_list=combined_qubits_list,
            )
            assert job.job_info.result.sampling.counts == counts
            assert divided_counts == expected_divided


# ---------------------------------------------------------------------------
# MultiManualStep.__init__
# ---------------------------------------------------------------------------


class TestMultiManualStepInit:
    @patch("oqtopus_engine_core.steps.multi_manual_step.grpc.aio.insecure_channel")
    @patch("oqtopus_engine_core.steps.multi_manual_step.combiner_pb2_grpc.CombinerServiceStub")
    def test_init_creates_channel_and_stub(
        self, mock_stub_cls: MagicMock, mock_channel: MagicMock,
    ) -> None:
        mock_channel.return_value = MagicMock()
        step = MultiManualStep(combiner_address="host:1234")
        mock_channel.assert_called_once_with("host:1234")
        mock_stub_cls.assert_called_once_with(mock_channel.return_value)
        assert step._channel is mock_channel.return_value  # noqa: SLF001

    @patch("oqtopus_engine_core.steps.multi_manual_step.grpc.aio.insecure_channel")
    @patch("oqtopus_engine_core.steps.multi_manual_step.combiner_pb2_grpc.CombinerServiceStub")
    def test_init_default_address(
        self,
        _mock_stub_cls: MagicMock,  # noqa: PT019
        mock_channel: MagicMock,
    ) -> None:
        MultiManualStep()
        mock_channel.assert_called_once_with("localhost:5002")


# ---------------------------------------------------------------------------
# MultiManualStep.pre_process
# ---------------------------------------------------------------------------


class TestMultiManualStepPreProcess:
    @pytest.fixture
    def step_and_stub(self) -> tuple[MultiManualStep, AsyncMock]:
        patch_channel = patch(
            "oqtopus_engine_core.steps.multi_manual_step"
            ".grpc.aio.insecure_channel",
        )
        patch_stub = patch(
            "oqtopus_engine_core.steps.multi_manual_step"
            ".combiner_pb2_grpc.CombinerServiceStub",
        )
        with patch_channel, patch_stub as mock_stub_cls:
            mock_stub: AsyncMock = AsyncMock()
            mock_stub_cls.return_value = mock_stub
            step: MultiManualStep = MultiManualStep(
                combiner_address="localhost:5002",
            )
        return step, mock_stub

    @pytest.mark.asyncio
    async def test_skip_non_multi_manual_job(
        self, step_and_stub: tuple[MultiManualStep, AsyncMock],
    ) -> None:
        """Non-multi_manual job should be skipped."""
        step, mock_stub = step_and_stub
        job: Job = _make_job(job_type="sampling")
        gctx: GlobalContext = _make_gctx()
        jctx: dict[str, Any] = {}

        await step.pre_process(gctx, jctx, job)

        mock_stub.Combine.assert_not_awaited()
        assert COMBINED_QUBITS_LIST_KEY not in jctx

    @pytest.mark.asyncio
    async def test_pre_process_success(
        self, step_and_stub: tuple[MultiManualStep, AsyncMock],
    ) -> None:
        """Successful combine should update job and jctx."""
        step, mock_stub = step_and_stub
        job: Job = _make_job()
        gctx: GlobalContext = _make_gctx()
        jctx: dict[str, Any] = {}

        mock_stub.Combine.return_value = _make_combine_response(
            status=0,
            combined_program="combined_qasm_result",
            combined_qubits_list=[1, 3],
        )

        await step.pre_process(gctx, jctx, job)

        assert job.job_info.combined_program == "combined_qasm_result"
        assert jctx[COMBINED_QUBITS_LIST_KEY] == [1, 3]
        assert jctx["max_qubits"] == 5
        assert jctx["combined_program"] == "combined_qasm_result"
        gctx.job_repository.update_job_info_nowait.assert_awaited_once_with(job)

    @pytest.mark.asyncio
    async def test_pre_process_failure_status(
        self, step_and_stub: tuple[MultiManualStep, AsyncMock],
    ) -> None:
        """STATUS_FAILURE should raise RuntimeError with generic message."""
        step, mock_stub = step_and_stub
        job: Job = _make_job()
        gctx: GlobalContext = _make_gctx()

        mock_stub.Combine.return_value = _make_combine_response(status=1)

        with pytest.raises(RuntimeError, match="failed to combine programs"):
            await step.pre_process(gctx, {}, job)

    @pytest.mark.asyncio
    async def test_pre_process_invalid_qubit_size_status(
        self, step_and_stub: tuple[MultiManualStep, AsyncMock],
    ) -> None:
        """STATUS_INVALID_QUBIT_SIZE should raise with specific message."""
        step, mock_stub = step_and_stub
        job: Job = _make_job()
        gctx: GlobalContext = _make_gctx()

        mock_stub.Combine.return_value = _make_combine_response(status=2)

        with pytest.raises(RuntimeError, match="invalid qubit size"):
            await step.pre_process(gctx, {}, job)

    @pytest.mark.asyncio
    async def test_pre_process_sends_correct_request(
        self, step_and_stub: tuple[MultiManualStep, AsyncMock],
    ) -> None:
        """Should send programs as JSON and max_qubits from device_info."""
        step, mock_stub = step_and_stub
        programs: list[str] = ["OPENQASM 3; h q[0];", "OPENQASM 3; cx q[0],q[1];"]
        job: Job = _make_job(job_info={"program": programs})
        gctx: GlobalContext = _make_gctx()

        mock_stub.Combine.return_value = _make_combine_response(status=0)

        await step.pre_process(gctx, {}, job)

        call_args = mock_stub.Combine.call_args
        request = call_args[0][0]
        assert json.loads(request.programs) == programs
        assert request.max_qubits == 5


# ---------------------------------------------------------------------------
# MultiManualStep.post_process
# ---------------------------------------------------------------------------


class TestMultiManualStepPostProcess:
    @pytest.fixture
    def step(self) -> MultiManualStep:
        patch_channel = patch(
            "oqtopus_engine_core.steps.multi_manual_step"
            ".grpc.aio.insecure_channel",
        )
        patch_stub = patch(
            "oqtopus_engine_core.steps.multi_manual_step"
            ".combiner_pb2_grpc.CombinerServiceStub",
        )
        with patch_channel, patch_stub:
            return MultiManualStep()

    @pytest.mark.asyncio
    async def test_skip_non_multi_manual_job(self, step: MultiManualStep) -> None:
        job: Job = _make_job(job_type="sampling")
        gctx: GlobalContext = _make_gctx()
        jctx: dict[str, Any] = {}

        await step.post_process(gctx, jctx, job)
        # No exception, no changes
        assert job == _make_job(job_type="sampling")
        assert jctx == {}

    @pytest.mark.asyncio
    async def test_post_process_divides_result(self, step: MultiManualStep) -> None:
        """Should divide counts based on combined_qubits_list from jctx."""
        job_result = JobResult(
            sampling={"counts": {"0001": 10, "1110": 20}, "divided_counts": None},
            estimation=None,
        )
        job: Job = _make_job(
            job_info={
                "program": ["p1", "p2"],
                "result": job_result,
                "message": None,
                "transpile_result": None,
                "operator": [],
            },
            status="succeeded",
        )
        gctx: GlobalContext = _make_gctx()
        jctx: dict[str, Any] = {COMBINED_QUBITS_LIST_KEY: [1, 3]}

        await step.post_process(gctx, jctx, job)

        assert job.job_info.result.sampling.divided_counts is not None
        # reversed [1,3] -> [3,1]
        # "0001" -> split("0001",[3,1]) -> ["000","1"] -> circuit1="000", circuit0="1"
        # "1110" -> split("1110",[3,1]) -> ["111","0"] -> circuit1="111", circuit0="0"
        assert job.job_info.result.sampling.divided_counts == {
            0: {"1": 10, "0": 20},
            1: {"000": 10, "111": 20},
        }

    @pytest.mark.asyncio
    async def test_post_process_empty_qubits_list_fallback(
        self, step: MultiManualStep,
    ) -> None:
        """When combined_qubits_list is missing, should fall back to empty dict."""
        job_result = JobResult(
            sampling={"counts": {"01": 5}, "divided_counts": None},
            estimation=None,
        )
        job = _make_job(
            job_info={
                "program": ["p"],
                "result": job_result,
                "message": None,
                "transpile_result": None,
                "operator": [],
            },
            status="succeeded",
        )
        gctx: GlobalContext = _make_gctx()
        jctx: dict[str, Any] = {}  # no COMBINED_QUBITS_LIST_KEY

        await step.post_process(gctx, jctx, job)

        # divide_result raises for empty list with non-empty counts → caught
        assert job.job_info.result.sampling.divided_counts == {}

    @pytest.mark.asyncio
    async def test_post_process_divide_error_sets_empty(
        self, step: MultiManualStep,
    ) -> None:
        """When divide_result raises, divided_counts should be set to {}."""
        job_result = JobResult(
            sampling={"counts": {}, "divided_counts": None},
            estimation=None,
        )
        job = _make_job(
            job_info={
                "program": ["p"],
                "result": job_result,
                "message": None,
                "transpile_result": None,
                "operator": [],
            },
            status="succeeded",
        )
        gctx: GlobalContext = _make_gctx()
        jctx: dict[str, Any] = {COMBINED_QUBITS_LIST_KEY: [2]}

        await step.post_process(gctx, jctx, job)

        # Empty counts raises ValueError, caught by except block
        assert job.job_info.result.sampling.divided_counts == {}
