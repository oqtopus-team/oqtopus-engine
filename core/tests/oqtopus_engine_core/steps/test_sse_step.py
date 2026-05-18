# ruff: noqa: SLF001, S108
from __future__ import annotations

import asyncio
import base64
import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import docker.errors  # type: ignore[import]
import pytest

from oqtopus_engine_core.framework import GlobalContext, Job
from oqtopus_engine_core.framework.job_repository import JobRepository
from oqtopus_engine_core.steps.sse_step import SseRunner, SseRuntimeError, SseStep

_MakeRunner = Callable[..., tuple[SseRunner, MagicMock, GlobalContext]]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_DEFAULTS: dict = {
    "device_id": "test-device",
    "shots": 1024,
    "transpiler_info": {},
    "simulator_info": {},
    "mitigation_info": {},
    "job_info": {
        "program": ["test_sse_program"],
        "transpile_result": None,
        "message": None,
        "result": None,
        "operator": [],
    },
}


def _make_job(**overrides: Any) -> Job:
    """Create a Job instance with sensible defaults.

    Any field can be overridden via keyword arguments.
    Nested 'job_info' can be passed as a dict and will be merged
    with defaults.

    Returns:
        A Job instance with the given overrides applied.
    """
    data = {**_JOB_DEFAULTS, **overrides}
    # Allow partial job_info overrides
    if "job_info" in overrides and isinstance(overrides["job_info"], dict):
        data["job_info"] = {**_JOB_DEFAULTS["job_info"], **overrides["job_info"]}
    return Job(**data)


def _make_gctx() -> GlobalContext:
    """Create a GlobalContext with a mocked JobRepository.

    Returns:
        A GlobalContext with an AsyncMock JobRepository.
    """
    mock_repo = AsyncMock(spec=JobRepository)
    return GlobalContext(config={}, job_repository=mock_repo)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner_settings(tmp_path: Path) -> dict:
    return {
        "host_work_path": str(tmp_path / "work"),
        "container_work_path": "/sse",
        "userprogram_name": "main.py",
        "result_file_name": "result.json",
        "log_file_name": "log.txt",
        "delete_host_temp_dirs": True,
        "container_image": "sse-runtime:latest",
        "sse_engine_address": "localhost:50051",
        "timeout": 600,
        "max_file_size": 10_000_000,
        "container_disk_quota": 67108864,
        "container_memory": 268435456,
        "container_cpu_set": "0",
        "container_network": "sse_net",
    }


@pytest.fixture
def sse_step(runner_settings: dict) -> SseStep:
    return SseStep(runner_settings=runner_settings)


@pytest.fixture
def mock_gctx() -> GlobalContext:
    return _make_gctx()


@pytest.fixture
def sample_job() -> Job:
    return _make_job(job_id="test-job-001", job_type="sse", status="ready")


@pytest.fixture
def non_sse_job() -> Job:
    return _make_job(job_id="test-job-002", job_type="sampling", status="ready")


# ---------------------------------------------------------------------------
# SseStep tests
# ---------------------------------------------------------------------------


class TestSseStepInit:
    def test_init_stores_settings(self, runner_settings: dict) -> None:
        step = SseStep(runner_settings=runner_settings)
        assert step._settings is runner_settings


class TestSseStepPreProcess:
    @pytest.mark.asyncio
    async def test_skip_non_sse_job(
        self, sse_step: SseStep, mock_gctx: GlobalContext, non_sse_job: Job
    ) -> None:
        """non-sse job should be skipped without any side effects."""
        jctx: dict[str, object] = {}
        await sse_step.pre_process(mock_gctx, jctx, non_sse_job)

        assert non_sse_job.status == "ready"  # status should be unchanged
        mock_gctx.job_repository.update_job_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pre_process_updates_status_to_running(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
    ) -> None:
        """pre_process should set job.status to 'running' and call update_job_status."""
        userprogram = base64.b64encode(b"test_sse_program").decode()
        # get_ssesrc returns repr(bytes) — see _download_userprogram
        mock_gctx.job_repository.get_ssesrc.return_value = repr(
            userprogram.encode("utf-8")
        )

        with patch.object(
            sse_step, "_run_sse", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = None
            await sse_step.pre_process(mock_gctx, {}, sample_job)

        assert sample_job.status == "running"
        mock_gctx.job_repository.update_job_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pre_process_calls_run_sse(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
    ) -> None:
        """pre_process should call _run_sse for an sse job."""
        userprogram = base64.b64encode(b"test_sse_program").decode()
        mock_gctx.job_repository.get_ssesrc.return_value = repr(
            userprogram.encode("utf-8")
        )

        with patch.object(
            sse_step, "_run_sse", new_callable=AsyncMock
        ) as mock_run:
            await sse_step.pre_process(mock_gctx, {}, sample_job)
            mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pre_process_raises_on_run_sse_failure(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
    ) -> None:
        """pre_process should propagate RuntimeError from _run_sse."""
        userprogram = base64.b64encode(b"test_sse_program").decode()
        mock_gctx.job_repository.get_ssesrc.return_value = repr(
            userprogram.encode("utf-8")
        )

        with (
            patch.object(
                sse_step,
                "_run_sse",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            pytest.raises(RuntimeError, match="fail"),
        ):
            await sse_step.pre_process(mock_gctx, {}, sample_job)

    @pytest.mark.asyncio
    async def test_pre_process_unexpected_error_wraps_as_runtime(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
    ) -> None:
        """Unexpected exceptions should be wrapped.

        Wraps as RuntimeError('internal server error').
        """
        userprogram = base64.b64encode(b"test_sse_program").decode()
        mock_gctx.job_repository.get_ssesrc.return_value = repr(
            userprogram.encode("utf-8")
        )

        with (
            patch.object(
                sse_step,
                "_run_sse",
                new_callable=AsyncMock,
                side_effect=ValueError("unexpected"),
            ),
            pytest.raises(RuntimeError, match="internal server error"),
        ):
            await sse_step.pre_process(mock_gctx, {}, sample_job)

    @pytest.mark.asyncio
    async def test_pre_process_cleans_up_temp_dirs(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """Temp directories should be cleaned up after pre_process completes."""
        sse_step._settings["host_work_path"] = str(tmp_path / "work")
        userprogram = base64.b64encode(b"test_sse_program").decode()
        mock_gctx.job_repository.get_ssesrc.return_value = repr(
            userprogram.encode("utf-8")
        )

        with patch.object(
            sse_step, "_run_sse", new_callable=AsyncMock
        ):
            await sse_step.pre_process(mock_gctx, {}, sample_job)

        # temp dirs should have been deleted
        base_dir = tmp_path / "work" / sample_job.job_id
        assert not base_dir.exists()

    @pytest.mark.asyncio
    async def test_pre_process_keeps_temp_dirs_when_config_false(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """When delete_host_temp_dirs=False, temp dirs should remain."""
        sse_step._settings["host_work_path"] = str(tmp_path / "work")
        sse_step._settings["delete_host_temp_dirs"] = False
        userprogram = base64.b64encode(b"test_sse_program").decode()
        mock_gctx.job_repository.get_ssesrc.return_value = repr(
            userprogram.encode("utf-8")
        )

        with patch.object(
            sse_step, "_run_sse", new_callable=AsyncMock
        ):
            await sse_step.pre_process(mock_gctx, {}, sample_job)

        base_dir = tmp_path / "work" / sample_job.job_id
        assert base_dir.exists()


class TestSseStepPostProcess:
    @pytest.mark.asyncio
    async def test_post_process_does_nothing(
        self, sse_step: SseStep, mock_gctx: GlobalContext, sample_job: Job
    ) -> None:
        """post_process should be a no-op."""
        await sse_step.post_process(mock_gctx, {}, sample_job)
        # No exception means success


# ---------------------------------------------------------------------------
# SseStep static / helper method tests
# ---------------------------------------------------------------------------


class TestMakeTmpdir:
    def test_creates_directories(self, tmp_path: Path) -> None:
        paths = SseStep._make_tmpdir("job-1", str(tmp_path))
        assert paths["base"].exists()
        assert paths["in"].exists()
        assert paths["out"].exists()
        assert paths["base"] == tmp_path / "job-1"
        assert paths["in"] == tmp_path / "job-1" / "in"
        assert paths["out"] == tmp_path / "job-1" / "out"

    def test_exists_ok(self, tmp_path: Path) -> None:
        """Calling twice should not raise."""
        SseStep._make_tmpdir("job-1", str(tmp_path))
        SseStep._make_tmpdir("job-1", str(tmp_path))


class TestDeleteTmpdir:
    def test_deletes_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "to_delete"
        target.mkdir()
        (target / "file.txt").write_text("hello")
        SseStep._delete_tmpdir(target)
        assert not target.exists()

    def test_non_existent_dir_no_error(self, tmp_path: Path) -> None:
        target = tmp_path / "non_existent"
        SseStep._delete_tmpdir(target)  # no error


class TestDownloadUserprogram:
    @pytest.mark.asyncio
    async def test_download_decodes_body(self) -> None:
        body_content = "hello world"
        body_bytes = body_content.encode("utf-8")
        # get_ssesrc returns repr(bytes)
        mock_repo = AsyncMock()
        mock_repo.get_ssesrc.return_value = repr(body_bytes)

        result = await SseStep._download_userprogram("job-1", mock_repo)
        assert result == body_content
        mock_repo.get_ssesrc.assert_awaited_once_with(job_id="job-1")


class TestMakeUserprogramFile:
    @pytest.mark.asyncio
    async def test_writes_decoded_file(self, tmp_path: Path) -> None:
        content = b"print('hello world')"
        encoded = base64.b64encode(content).decode()
        await SseStep._make_userprogram_file(encoded, tmp_path, "main.py")
        written = (tmp_path / "main.py").read_bytes()
        assert written == content

    @pytest.mark.asyncio
    async def test_invalid_base64_raises(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="padding"):
            await SseStep._make_userprogram_file(
                "not-valid-base64!!!", tmp_path, "main.py"
            )


class TestSetResultToJob:
    def test_sets_fields_from_result_job(self) -> None:
        job = _make_job(job_id="j1", job_type="sse", status="running")
        result_job = _make_job(
            job_id="j1", job_type="sse", status="succeeded",
            transpiler_info={"key": "value"},
            job_info={"program": ["result_prog"], "message": "ok"},
        )

        SseStep._set_result_to_job(job, result_job, 1.5)

        assert job.job_info == result_job.job_info
        assert job.transpiler_info == {"key": "value"}
        assert job.execution_time == 1.5

    def test_none_result_job_noop(self) -> None:
        job = _make_job(job_id="j2", job_type="sse", status="running")
        original_info = job.job_info
        SseStep._set_result_to_job(job, None, 2.0)
        assert job.job_info == original_info

    def test_result_job_info_none_does_not_overwrite(self) -> None:
        job = _make_job(job_id="j3", job_type="sse", status="running")
        original_info = job.job_info
        result_job = MagicMock()
        result_job.job_info = None
        result_job.transpiler_info = {"t": "info"}

        SseStep._set_result_to_job(job, result_job, 3.0)
        assert job.job_info == original_info
        assert job.transpiler_info == {"t": "info"}
        assert job.execution_time == 3.0


# ---------------------------------------------------------------------------
# SseStep._run_sse tests
# ---------------------------------------------------------------------------


class TestRunSse:
    @pytest.mark.asyncio
    async def test_run_sse_success(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """Successful SSE run should update job status & transpiler info."""
        temp_dirs = SseStep._make_tmpdir(sample_job.job_id, str(tmp_path))

        result_job = _make_job(
            job_id=sample_job.job_id, job_type="sse", status="succeeded",
            transpiler_info={"transpiled": True},
        )

        mock_runner = MagicMock()
        mock_runner.run_sse = AsyncMock()
        mock_runner.result_job = result_job

        with patch(
            "oqtopus_engine_core.steps.sse_step.SseRunner",
            return_value=mock_runner,
        ):
            await sse_step._run_sse(
                sample_job, mock_gctx, sse_step._settings, temp_dirs
            )

        assert sample_job.status == "succeeded"
        mock_gctx.job_repository.update_job_transpiler_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sse_user_program_error_raises(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """SseRuntimeError from run_sse should be wrapped as RuntimeError."""
        temp_dirs = SseStep._make_tmpdir(sample_job.job_id, str(tmp_path))

        mock_runner = MagicMock()
        mock_runner.run_sse = AsyncMock(side_effect=SseRuntimeError("user error"))
        mock_runner.result_job = None

        with patch(
            "oqtopus_engine_core.steps.sse_step.SseRunner",
            return_value=mock_runner,
        ), pytest.raises(RuntimeError, match="user program execution error"):
            await sse_step._run_sse(
                sample_job, mock_gctx, sse_step._settings, temp_dirs
            )

    @pytest.mark.asyncio
    async def test_run_sse_generic_error_raises_internal(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """Generic exception from run_sse should be wrapped as internal server error."""
        temp_dirs = SseStep._make_tmpdir(sample_job.job_id, str(tmp_path))

        mock_runner = MagicMock()
        mock_runner.run_sse = AsyncMock(side_effect=OSError("network"))
        mock_runner.result_job = None

        with patch(
            "oqtopus_engine_core.steps.sse_step.SseRunner",
            return_value=mock_runner,
        ), pytest.raises(RuntimeError, match="internal server error"):
            await sse_step._run_sse(
                sample_job, mock_gctx, sse_step._settings, temp_dirs
            )

    @pytest.mark.asyncio
    async def test_run_sse_non_succeeded_status_raises(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """When run_sse completes but status != 'succeeded'.

        Should raise RuntimeError.
        """
        temp_dirs = SseStep._make_tmpdir(sample_job.job_id, str(tmp_path))

        # Set message on the input job (the code reads job.job_info.message
        # before _set_result_to_job copies result_job fields)
        sample_job.job_info.message = "custom failure message"

        result_job = _make_job(
            job_id=sample_job.job_id, job_type="sse", status="failed",
        )

        mock_runner = MagicMock()
        mock_runner.run_sse = AsyncMock()
        mock_runner.result_job = result_job

        with patch(
            "oqtopus_engine_core.steps.sse_step.SseRunner",
            return_value=mock_runner,
        ), pytest.raises(RuntimeError, match="custom failure message"):
            await sse_step._run_sse(
                sample_job, mock_gctx, sse_step._settings, temp_dirs
            )

    @pytest.mark.asyncio
    async def test_run_sse_init_failure_raises_internal(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """When SseRunner __init__ fails, should raise RuntimeError."""
        temp_dirs = SseStep._make_tmpdir(sample_job.job_id, str(tmp_path))

        with patch(
            "oqtopus_engine_core.steps.sse_step.SseRunner",
            side_effect=Exception("docker not found"),
        ), pytest.raises(RuntimeError, match="internal server error"):
            await sse_step._run_sse(
                sample_job, mock_gctx, sse_step._settings, temp_dirs
            )


# ---------------------------------------------------------------------------
# SseRunner tests
# ---------------------------------------------------------------------------


class TestSseRunnerIsFileSizeValid:
    def test_valid_size(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert SseRunner._is_file_size_valid(f, 1000) is True

    def test_exceeds_max_size(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 100)
        assert SseRunner._is_file_size_valid(f, 50) is False

    def test_file_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.txt"
        with pytest.raises(FileNotFoundError):
            SseRunner._is_file_size_valid(f, 1000)

    def test_is_directory(self, tmp_path: Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        with pytest.raises(IsADirectoryError):
            SseRunner._is_file_size_valid(d, 1000)

    def test_exactly_at_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "exact.txt"
        f.write_bytes(b"x" * 100)
        assert SseRunner._is_file_size_valid(f, 100) is True

    def test_one_over_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "over.txt"
        f.write_bytes(b"x" * 101)
        assert SseRunner._is_file_size_valid(f, 100) is False


class TestSseRunnerInit:
    @patch("oqtopus_engine_core.steps.sse_step.docker")
    def test_init_success(
        self, _mock_docker: MagicMock, runner_settings: dict, sample_job: Job  # noqa: PT019
    ) -> None:
        gctx = _make_gctx()
        temp_dirs = {
            "base": Path("/tmp/work/job-1"),
            "in": Path("/tmp/work/job-1/in"),
            "out": Path("/tmp/work/job-1/out"),
        }
        runner = SseRunner(runner_settings, temp_dirs, sample_job, gctx)
        assert runner._job_id == sample_job.job_id
        assert runner._container_name == sample_job.job_id
        assert runner.result_job is None


class TestSseRunnerRunSse:
    @pytest.mark.asyncio
    @patch("oqtopus_engine_core.steps.sse_step.docker")
    async def test_run_sse_full_success(
        self, _mock_docker: MagicMock, runner_settings: dict, sample_job: Job  # noqa: PT019
    ) -> None:
        """Full success path: preprocess -> exec -> postprocess -> stop/remove."""
        gctx = _make_gctx()
        temp_dirs = {
            "base": Path("/tmp/work/job-1"),
            "in": Path("/tmp/work/job-1/in"),
            "out": Path("/tmp/work/job-1/out"),
        }
        runner = SseRunner(runner_settings, temp_dirs, sample_job, gctx)

        runner._preprocess_container = AsyncMock()
        runner._exec_in_container = AsyncMock()
        runner._postprocess_container = AsyncMock()
        runner._stop_and_remove = MagicMock()

        await runner.run_sse()

        runner._preprocess_container.assert_awaited_once()
        runner._exec_in_container.assert_awaited_once()
        runner._postprocess_container.assert_awaited_once_with(exec_is_success=True)
        runner._stop_and_remove.assert_called_once()

    @pytest.mark.asyncio
    @patch("oqtopus_engine_core.steps.sse_step.docker")
    async def test_run_sse_preprocess_failure(
        self, _mock_docker: MagicMock, runner_settings: dict, sample_job: Job  # noqa: PT019
    ) -> None:
        """When preprocess fails, should propagate exception."""
        gctx = _make_gctx()
        temp_dirs = {
            "base": Path("/tmp/work/job-1"),
            "in": Path("/tmp/work/job-1/in"),
            "out": Path("/tmp/work/job-1/out"),
        }
        runner = SseRunner(runner_settings, temp_dirs, sample_job, gctx)
        runner._preprocess_container = AsyncMock(
            side_effect=RuntimeError("container start failed")
        )

        with pytest.raises(RuntimeError, match="container start failed"):
            await runner.run_sse()

    @pytest.mark.asyncio
    @patch("oqtopus_engine_core.steps.sse_step.docker")
    async def test_run_sse_exec_failure_raises_sse_runtime_error(
        self, _mock_docker: MagicMock, runner_settings: dict, sample_job: Job  # noqa: PT019
    ) -> None:
        """When user program execution fails, should raise SseRuntimeError."""
        gctx = _make_gctx()
        temp_dirs = {
            "base": Path("/tmp/work/job-1"),
            "in": Path("/tmp/work/job-1/in"),
            "out": Path("/tmp/work/job-1/out"),
        }
        runner = SseRunner(runner_settings, temp_dirs, sample_job, gctx)
        runner._preprocess_container = AsyncMock()
        runner._exec_in_container = AsyncMock(side_effect=RuntimeError("exec failed"))
        runner._postprocess_container = AsyncMock()
        runner._stop_and_remove = MagicMock()

        with pytest.raises(SseRuntimeError, match="user program execution failed"):
            await runner.run_sse()

        runner._postprocess_container.assert_awaited_once_with(exec_is_success=False)
        runner._stop_and_remove.assert_called_once()

    @pytest.mark.asyncio
    @patch("oqtopus_engine_core.steps.sse_step.docker")
    async def test_run_sse_postprocess_failure_raises(
        self, _mock_docker: MagicMock, runner_settings: dict, sample_job: Job  # noqa: PT019
    ) -> None:
        """When postprocess fails (but exec succeeds), should raise RuntimeError."""
        gctx = _make_gctx()
        temp_dirs = {
            "base": Path("/tmp/work/job-1"),
            "in": Path("/tmp/work/job-1/in"),
            "out": Path("/tmp/work/job-1/out"),
        }
        runner = SseRunner(runner_settings, temp_dirs, sample_job, gctx)
        runner._preprocess_container = AsyncMock()
        runner._exec_in_container = AsyncMock()
        runner._postprocess_container = AsyncMock(
            side_effect=RuntimeError("postprocess error")
        )
        runner._stop_and_remove = MagicMock()

        with pytest.raises(RuntimeError, match="sse result retrieval failed"):
            await runner.run_sse()

        runner._stop_and_remove.assert_called_once()


class TestSseRuntimeError:
    def test_is_runtime_error(self) -> None:
        err = SseRuntimeError("test")
        assert isinstance(err, RuntimeError)
        assert str(err) == "test"


# ---------------------------------------------------------------------------
# Helper: create a SseRunner instance with docker mocked
# ---------------------------------------------------------------------------

@pytest.fixture
def make_runner(
    runner_settings: dict, sample_job: Job
) -> _MakeRunner:
    """Factory that returns (runner, mock_docker_module) with docker patched.

    Returns:
        A callable that creates a tuple of (SseRunner, mock_docker, gctx).
    """
    def _make(
        **overrides: Any,
    ) -> tuple[SseRunner, MagicMock, GlobalContext]:
        gctx = _make_gctx()
        temp_dirs = {
            "base": Path("/tmp/work") / sample_job.job_id,
            "in": Path("/tmp/work") / sample_job.job_id / "in",
            "out": Path("/tmp/work") / sample_job.job_id / "out",
        }
        with patch("oqtopus_engine_core.steps.sse_step.docker") as mock_docker:
            settings = {**runner_settings, **overrides}
            runner = SseRunner(settings, temp_dirs, sample_job, gctx)
        return runner, mock_docker, gctx
    return _make


# ---------------------------------------------------------------------------
# _make_tmpdir error path
# ---------------------------------------------------------------------------

class TestMakeTmpdirErrors:
    def test_mkdir_failure_raises(self, tmp_path: Path) -> None:
        """When mkdir fails, exception should propagate."""
        with (
            patch.object(
                Path, "mkdir", side_effect=OSError("permission denied")
            ),
            pytest.raises(OSError, match="permission denied"),
        ):
            SseStep._make_tmpdir("job-err", str(tmp_path))


# ---------------------------------------------------------------------------
# _delete_tmpdir error path
# ---------------------------------------------------------------------------

class TestDeleteTmpdirErrors:
    def test_rmtree_failure_raises_runtime(self, tmp_path: Path) -> None:
        target = tmp_path / "fail_delete"
        target.mkdir()
        with (
            patch(
                "oqtopus_engine_core.steps.sse_step.shutil.rmtree",
                side_effect=OSError("busy"),
            ),
            pytest.raises(
                RuntimeError, match="failed to delete temp directory"
            ),
        ):
            SseStep._delete_tmpdir(target)


# ---------------------------------------------------------------------------
# _make_userprogram_file error paths
# ---------------------------------------------------------------------------

class TestMakeUserprogramFileErrors:
    @pytest.mark.asyncio
    async def test_write_failure_raises(self, tmp_path: Path) -> None:
        content = b"test_sse_program"
        encoded = base64.b64encode(content).decode()
        with (
            patch.object(
                Path, "write_bytes", side_effect=OSError("disk full")
            ),
            pytest.raises(OSError, match="disk full"),
        ):
            await SseStep._make_userprogram_file(
                encoded, tmp_path, "main.py"
            )

    @pytest.mark.asyncio
    async def test_file_permission_is_set(self, tmp_path: Path) -> None:
        content = b"test_sse_program"
        encoded = base64.b64encode(content).decode()
        await SseStep._make_userprogram_file(encoded, tmp_path, "main.py")
        mode = (tmp_path / "main.py").stat().st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# _prep_userprogram
# ---------------------------------------------------------------------------

class TestPrepUserprogram:
    @pytest.mark.asyncio
    async def test_prep_userprogram_downloads_and_writes(
        self, sse_step: SseStep, tmp_path: Path
    ) -> None:
        content = b"print('sse')"
        encoded = base64.b64encode(content).decode()
        mock_repo = AsyncMock()
        mock_repo.get_ssesrc.return_value = repr(encoded.encode("utf-8"))

        in_dir = tmp_path / "in"
        in_dir.mkdir()
        await sse_step._prep_userprogram("job-1", mock_repo, in_dir, "main.py")

        written = (in_dir / "main.py").read_bytes()
        assert written == content


# ---------------------------------------------------------------------------
# SseRunner._start_container
# ---------------------------------------------------------------------------

class TestStartContainer:
    @patch("oqtopus_engine_core.steps.sse_step.docker")
    def test_start_container_calls_docker_run(
        self,
        mock_docker: MagicMock,
        runner_settings: dict,
        sample_job: Job,
    ) -> None:
        gctx = _make_gctx()
        temp_dirs = {
            "base": Path("/tmp/w/j"), "in": Path("/tmp/w/j/in"),
            "out": Path("/tmp/w/j/out"),
        }
        mock_container = MagicMock()
        mock_container.id = "abc123"
        docker_client = mock_docker.DockerClient.return_value
        docker_client.containers.run.return_value = mock_container

        runner = SseRunner(runner_settings, temp_dirs, sample_job, gctx)
        result = runner._start_container()

        assert result is mock_container
        mock_docker.DockerClient.return_value.containers.run.assert_called_once()
        call_kwargs = mock_docker.DockerClient.return_value.containers.run.call_args
        assert call_kwargs.kwargs["name"] == sample_job.job_id
        assert call_kwargs.kwargs["image"] == runner_settings["container_image"]
        assert call_kwargs.kwargs["detach"] is True


# ---------------------------------------------------------------------------
# SseRunner._exec_in_container
# ---------------------------------------------------------------------------

class TestExecInContainer:
    @pytest.mark.asyncio
    async def test_exec_success(self, make_runner: _MakeRunner) -> None:
        runner, _mock_docker, _ = make_runner()
        runner._container = MagicMock()
        runner._container.exec_run.return_value = (0, b"ok")

        await runner._exec_in_container(cmd="echo hi", user="root", privileged=True)
        runner._container.exec_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_exec_nonzero_exit_raises(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.exec_run.return_value = (1, b"error")

        with pytest.raises(RuntimeError, match="command execution failed"):
            await runner._exec_in_container(cmd="false", user="root", privileged=True)

    @pytest.mark.asyncio
    async def test_exec_timeout_raises(
        self, make_runner: _MakeRunner
    ) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()

        async def slow_thread(
            *_args: Any, **_kwargs: Any
        ) -> int:
            await asyncio.sleep(10)
            return 0

        with (
            patch("asyncio.to_thread", side_effect=slow_thread),
            pytest.raises(TimeoutError, match="timeout"),
        ):
            await runner._exec_in_container(
                cmd="sleep 100",
                user="root",
                privileged=True,
                timeout_in_sec=0.01,
            )

    @pytest.mark.asyncio
    async def test_exec_docker_api_error_returns_nonzero(
        self, make_runner: _MakeRunner
    ) -> None:
        runner, _mock_docker, _ = make_runner()
        runner._container = MagicMock()
        runner._container.exec_run.side_effect = docker.errors.APIError("api err")

        # exec_cmd catches APIError and returns -1, which causes RuntimeError
        with pytest.raises(RuntimeError, match="command execution failed"):
            await runner._exec_in_container(cmd="fail", user="root", privileged=True)

    @pytest.mark.asyncio
    async def test_exec_generic_exception_returns_nonzero(
        self, make_runner: _MakeRunner
    ) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.exec_run.side_effect = Exception("boom")

        with pytest.raises(RuntimeError, match="command execution failed"):
            await runner._exec_in_container(cmd="fail", user="root", privileged=True)


# ---------------------------------------------------------------------------
# SseRunner._copy_user_program_into_container
# ---------------------------------------------------------------------------

class TestCopyUserProgramIntoContainer:
    @pytest.mark.asyncio
    async def test_copy_success(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.put_archive.return_value = True
        runner._exec_in_container = AsyncMock()

        await runner._copy_user_program_into_container(
            user="appuser",
            user_program_name="main.py",
            user_program_content=b"print('hi')",
        )

        runner._container.put_archive.assert_called_once()
        runner._exec_in_container.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_copy_put_archive_fails(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.put_archive.return_value = False

        with pytest.raises(RuntimeError, match="failed to put archive"):
            await runner._copy_user_program_into_container(
                user="appuser",
                user_program_name="main.py",
                user_program_content=b"data",
            )


# ---------------------------------------------------------------------------
# SseRunner._get_result_from_container
# ---------------------------------------------------------------------------

def _make_tar_bytes(filename: str, content: bytes) -> list:
    """Create tar chunks simulating docker exec_run stream output.

    Returns:
        A list of (stdout, stderr) tuples.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        tar.addfile(tarinfo=info, fileobj=io.BytesIO(content))
    return [(buf.getvalue(), None)]


def _make_tar_bytes_directory(dirname: str) -> list:
    """Create tar chunks containing a directory entry.

    tar.extractfile() returns None for directory members,
    so this helper is used to test that edge case.

    Returns:
        A list of (stdout, stderr) tuples.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=dirname)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(tarinfo=info)
    return [(buf.getvalue(), None)]


class TestGetResultFromContainer:
    def test_success(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()

        job_data = {
            "job_id": "j1", "device_id": "dev", "job_type": "sse",
            "shots": 100, "status": "succeeded",
            "transpiler_info": {}, "simulator_info": {},
            "mitigation_info": {},
            "job_info": {
                "program": ["p"], "transpile_result": None,
                "message": None, "result": None, "operator": [],
            },
        }
        chunks = _make_tar_bytes("result.json", json.dumps(job_data).encode())
        runner._container.exec_run.return_value = (0, iter(chunks))

        result = runner._get_result_from_container(
            user="appuser", container_path=Path("/sse/out"), filename="result.json"
        )
        assert result.job_id == "j1"
        assert result.status == "succeeded"

    def test_stderr_raises(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.exec_run.return_value = (
            0, iter([(None, b"tar: file not found")])
        )

        with pytest.raises(RuntimeError, match="error when getting file"):
            runner._get_result_from_container(
                user="appuser", container_path=Path("/sse/out"), filename="result.json"
            )

    def test_empty_result_raises(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()

        chunks = _make_tar_bytes("result.json", b"")
        runner._container.exec_run.return_value = (0, iter(chunks))

        with pytest.raises(RuntimeError, match="result file is empty"):
            runner._get_result_from_container(
                user="appuser", container_path=Path("/sse/out"), filename="result.json"
            )

    def test_directory_entry_extractfile_returns_none(
        self, make_runner: _MakeRunner
    ) -> None:
        """When the tar member is a directory, extractfile() returns None.

        This covers the 'result file not found in tar' branch.
        """
        runner, _, _ = make_runner()
        runner._container = MagicMock()

        chunks = _make_tar_bytes_directory("result.json")
        runner._container.exec_run.return_value = (0, iter(chunks))

        with pytest.raises(RuntimeError, match="result file not found in tar"):
            runner._get_result_from_container(
                user="appuser", container_path=Path("/sse/out"), filename="result.json"
            )


# ---------------------------------------------------------------------------
# SseRunner._save_container_log
# ---------------------------------------------------------------------------

class TestSaveContainerLog:
    def test_save_log_writes_file(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.logs.return_value = b"log line 1\nlog line 2"

        runner._save_container_log(
            out_path=tmp_path, log_file_name="log.txt", print_to_engine_log=False
        )

        written = (tmp_path / "log.txt").read_text()
        assert "log line 1" in written
        assert "log line 2" in written

    def test_save_log_prints_when_flag_true(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.logs.return_value = b"error output"

        # Should not raise; logger.info is called internally
        runner._save_container_log(
            out_path=tmp_path, log_file_name="log.txt", print_to_engine_log=True
        )

        written = (tmp_path / "log.txt").read_text()
        assert "error output" in written


# ---------------------------------------------------------------------------
# SseRunner._stop_and_remove
# ---------------------------------------------------------------------------

class TestStopAndRemove:
    def test_success(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()

        runner._stop_and_remove()

        runner._container.stop.assert_called_once_with(timeout=0)
        runner._container.remove.assert_called_once_with(v=True, force=True)

    def test_stop_api_error_continues(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.stop.side_effect = docker.errors.APIError("stop err")

        # Should NOT raise, just log
        runner._stop_and_remove()
        runner._container.remove.assert_called_once()

    def test_stop_generic_error_continues(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.stop.side_effect = Exception("unexpected")

        runner._stop_and_remove()
        runner._container.remove.assert_called_once()

    def test_remove_api_error_does_not_raise(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.remove.side_effect = docker.errors.APIError("remove err")

        runner._stop_and_remove()  # should not raise

    def test_remove_generic_error_does_not_raise(
        self, make_runner: _MakeRunner
    ) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.remove.side_effect = Exception("remove boom")

        runner._stop_and_remove()  # should not raise

    def test_both_fail_does_not_raise(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._container = MagicMock()
        runner._container.stop.side_effect = docker.errors.APIError("s")
        runner._container.remove.side_effect = docker.errors.APIError("r")

        runner._stop_and_remove()  # should not raise


# ---------------------------------------------------------------------------
# SseRunner._s3upload
# ---------------------------------------------------------------------------

class TestS3Upload:
    @pytest.mark.asyncio
    async def test_upload_success(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, gctx = make_runner()
        log_file = tmp_path / "log.txt"
        log_file.write_text("log content")

        await runner._s3upload("job-1", tmp_path, "log.txt", 10_000_000)

        gctx.job_repository.update_sselog.assert_awaited_once_with(
            job_id="job-1", sselog=str(log_file)
        )

    @pytest.mark.asyncio
    async def test_upload_file_not_found(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()

        with pytest.raises(FileNotFoundError, match="file not found during S3 upload"):
            await runner._s3upload("job-1", tmp_path, "missing.txt", 10_000_000)

    @pytest.mark.asyncio
    async def test_upload_is_directory(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        (tmp_path / "dir").mkdir()

        with pytest.raises(IsADirectoryError, match="invalid file path"):
            await runner._s3upload("job-1", tmp_path, "dir", 10_000_000)

    @pytest.mark.asyncio
    async def test_upload_file_too_large(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        big = tmp_path / "big.txt"
        big.write_bytes(b"x" * 200)

        with pytest.raises(ValueError, match="file size exceeds"):
            await runner._s3upload("job-1", tmp_path, "big.txt", 100)

    @pytest.mark.asyncio
    async def test_upload_repository_failure(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, gctx = make_runner()
        log_file = tmp_path / "log.txt"
        log_file.write_text("content")
        gctx.job_repository.update_sselog.side_effect = Exception("s3 error")

        with pytest.raises(RuntimeError, match="failed to upload file to S3"):
            await runner._s3upload("job-1", tmp_path, "log.txt", 10_000_000)


# ---------------------------------------------------------------------------
# SseRunner._preprocess_container
# ---------------------------------------------------------------------------

class TestPreprocessContainer:
    @pytest.mark.asyncio
    async def test_success(self, make_runner: _MakeRunner, tmp_path: Path) -> None:
        runner, _, _ = make_runner()
        # Prepare user program file on host
        in_dir = tmp_path / "in"
        in_dir.mkdir(parents=True)
        (in_dir / "main.py").write_bytes(b"test_sse_program")
        runner._host_work_path = {"in": in_dir, "out": tmp_path / "out"}

        mock_container = MagicMock()
        mock_container.put_archive.return_value = True
        runner._start_container = MagicMock(return_value=mock_container)
        runner._exec_in_container = AsyncMock()
        runner._copy_user_program_into_container = AsyncMock()

        await runner._preprocess_container()

        runner._start_container.assert_called_once()
        assert runner._container is mock_container
        # init call + (copy handled by mocked method)
        runner._exec_in_container.assert_awaited_once()
        runner._copy_user_program_into_container.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_container_failure(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._start_container = MagicMock(side_effect=Exception("docker fail"))

        with pytest.raises(RuntimeError, match="failed to start SSE container"):
            await runner._preprocess_container()

    @pytest.mark.asyncio
    async def test_start_container_returns_none(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        runner._start_container = MagicMock(return_value=None)

        with pytest.raises(RuntimeError, match="failed to start SSE container"):
            await runner._preprocess_container()

    @pytest.mark.asyncio
    async def test_init_failure_stops_container(self, make_runner: _MakeRunner) -> None:
        runner, _, _ = make_runner()
        mock_container = MagicMock()
        runner._start_container = MagicMock(return_value=mock_container)
        runner._exec_in_container = AsyncMock(
            side_effect=RuntimeError("init fail")
        )
        runner._stop_and_remove = MagicMock()

        with pytest.raises(RuntimeError, match="failed to initialize SSE container"):
            await runner._preprocess_container()

        runner._stop_and_remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_copy_failure_stops_container(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        in_dir = tmp_path / "in"
        in_dir.mkdir(parents=True)
        (in_dir / "main.py").write_bytes(b"data")
        runner._host_work_path = {"in": in_dir, "out": tmp_path / "out"}

        mock_container = MagicMock()
        runner._start_container = MagicMock(return_value=mock_container)
        runner._exec_in_container = AsyncMock()  # init succeeds
        runner._copy_user_program_into_container = AsyncMock(
            side_effect=RuntimeError("copy fail")
        )
        runner._stop_and_remove = MagicMock()

        with pytest.raises(RuntimeError, match="failed to copy user program"):
            await runner._preprocess_container()

        runner._stop_and_remove.assert_called_once()


# ---------------------------------------------------------------------------
# SseRunner._postprocess_container
# ---------------------------------------------------------------------------

class TestPostprocessContainer:
    @pytest.mark.asyncio
    async def test_all_success(self, make_runner: _MakeRunner, tmp_path: Path) -> None:
        runner, _, _gctx = make_runner()
        runner._host_work_path = {"out": tmp_path}
        runner._container = MagicMock()

        result_job = _make_job(job_id="j1", job_type="sse", status="succeeded")
        runner._get_result_from_container = MagicMock(return_value=result_job)
        runner._save_container_log = MagicMock()
        runner._s3upload = AsyncMock()

        await runner._postprocess_container(exec_is_success=True)

        assert runner.result_job is result_job
        runner._save_container_log.assert_called_once()
        runner._s3upload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_result_failure_continues(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _gctx = make_runner()
        runner._host_work_path = {"out": tmp_path}
        runner._container = MagicMock()

        runner._get_result_from_container = MagicMock(
            side_effect=RuntimeError("no result")
        )
        runner._save_container_log = MagicMock()
        runner._s3upload = AsyncMock()

        # Should raise because copy_is_success is False
        with pytest.raises(RuntimeError, match="failed to get result"):
            await runner._postprocess_container(exec_is_success=True)

        # But log saving and upload should still have been attempted
        runner._save_container_log.assert_called_once()
        runner._s3upload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_log_failure_skips_upload(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        runner._host_work_path = {"out": tmp_path}
        runner._container = MagicMock()

        result_job = _make_job(job_id="j1", job_type="sse", status="succeeded")
        runner._get_result_from_container = MagicMock(return_value=result_job)
        runner._save_container_log = MagicMock(side_effect=RuntimeError("log fail"))
        runner._s3upload = AsyncMock()

        with pytest.raises(RuntimeError, match="failed to get result"):
            await runner._postprocess_container(exec_is_success=True)

        # upload should NOT be called when log saving fails
        runner._s3upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_failure(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        runner._host_work_path = {"out": tmp_path}
        runner._container = MagicMock()

        result_job = _make_job(job_id="j1", job_type="sse", status="succeeded")
        runner._get_result_from_container = MagicMock(return_value=result_job)
        runner._save_container_log = MagicMock()
        runner._s3upload = AsyncMock(side_effect=RuntimeError("upload fail"))

        with pytest.raises(RuntimeError, match="failed to get result"):
            await runner._postprocess_container(exec_is_success=True)

    @pytest.mark.asyncio
    async def test_exec_failure_flag_passed_to_log(
        self, make_runner: _MakeRunner, tmp_path: Path
    ) -> None:
        runner, _, _ = make_runner()
        runner._host_work_path = {"out": tmp_path}
        runner._container = MagicMock()

        result_job = _make_job(job_id="j1", job_type="sse", status="succeeded")
        runner._get_result_from_container = MagicMock(return_value=result_job)
        runner._save_container_log = MagicMock()
        runner._s3upload = AsyncMock()

        await runner._postprocess_container(exec_is_success=False)

        # print_to_engine_log should be True when exec_is_success=False
        call_args = runner._save_container_log.call_args
        # Called as positional: (out_path, log_file_name, print_to_engine_log)
        # or keyword. Check both styles.
        if len(call_args.args) >= 3:
            assert call_args.args[2] is True
        else:
            assert call_args.kwargs.get("print_to_engine_log") is True


# ---------------------------------------------------------------------------
# SseStep._run_sse - additional edge case: message is None falls back
# ---------------------------------------------------------------------------

class TestRunSseAdditional:
    @pytest.mark.asyncio
    async def test_run_sse_failed_with_no_message_uses_default(
        self,
        sse_step: SseStep,
        mock_gctx: GlobalContext,
        sample_job: Job,
        tmp_path: Path,
    ) -> None:
        """When status != 'succeeded' and message is None.

        Falls back to 'sse job failed'.
        """
        temp_dirs = SseStep._make_tmpdir(sample_job.job_id, str(tmp_path))

        sample_job.job_info.message = None

        result_job = _make_job(
            job_id=sample_job.job_id, job_type="sse", status="failed",
        )

        mock_runner = MagicMock()
        mock_runner.run_sse = AsyncMock()
        mock_runner.result_job = result_job

        with patch(
            "oqtopus_engine_core.steps.sse_step.SseRunner",
            return_value=mock_runner,
        ), pytest.raises(RuntimeError, match="sse job failed"):
            await sse_step._run_sse(
                sample_job, mock_gctx, sse_step._settings, temp_dirs
            )
