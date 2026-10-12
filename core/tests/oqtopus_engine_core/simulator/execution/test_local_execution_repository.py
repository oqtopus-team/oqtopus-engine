from pathlib import Path
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from oqtopus_engine_core.simulator import ExecutionState, LocalExecutionRepository


@pytest.mark.asyncio
async def test_local_repository_persists_child_lifecycle_and_cleans_artifacts(
    tmp_path: Path,
):
    repository = LocalExecutionRepository(tmp_path / "work")
    await repository.initialize()

    assert await repository.claim("child-1", "sampling")
    assert not await repository.claim("child-1", "sampling")

    work_dir = tmp_path / "work" / "child-artifacts"
    work_dir.mkdir()
    request_path = work_dir / "request.json"
    result_path = work_dir / "result.json"
    record = await repository.prepare(
        cloud_job_id="child-1",
        request_hash="request-digest",
        options={"n_nodes": 1},
        work_dir=work_dir,
        request_path=request_path,
        result_path=result_path,
    )
    assert record.state is ExecutionState.READY

    record = await repository.update(
        "child-1",
        ExecutionState.RUNNING,
        expected={ExecutionState.READY},
    )
    assert record.state is ExecutionState.RUNNING
    record = await repository.update(
        "child-1",
        ExecutionState.RESULT_READY,
        expected={ExecutionState.RUNNING},
    )
    assert record.state is ExecutionState.RESULT_READY
    record = await repository.update(
        "child-1",
        ExecutionState.SUCCEEDED,
        expected={ExecutionState.RESULT_READY},
    )
    assert record.state is ExecutionState.SUCCEEDED

    assert await repository.cleanup_artifacts("child-1", tmp_path / "work")
    assert not work_dir.exists()
    persisted = await repository.get("child-1")
    assert persisted is None
    assert not list((tmp_path / "work").glob("*.execution.json"))
    assert not await repository.cleanup_artifacts("child-1", tmp_path / "work")


@pytest.mark.asyncio
async def test_cleanup_candidates_survive_restart_and_include_metadata_only(tmp_path):
    repository = LocalExecutionRepository(tmp_path)
    await repository.claim("failed", "sampling", parent_job_id="root")
    await repository.claim("active", "sampling", parent_job_id="root")
    await repository.update("failed", ExecutionState.FAILED)
    (tmp_path / "broken.execution.json").write_text("{")
    restarted = LocalExecutionRepository(tmp_path)

    assert (
        await restarted.list_cleanup_candidates(datetime.now(UTC) - timedelta(days=1))
        == []
    )
    candidates = await restarted.list_cleanup_candidates(datetime.now(UTC))
    assert [record.cloud_job_id for record in candidates] == ["failed"]
    assert candidates[0].parent_job_id == "root"
    assert await restarted.cleanup_artifacts("failed", tmp_path)
    assert await restarted.get("failed") is None
    assert await restarted.get("active") is not None


@pytest.mark.asyncio
async def test_cleanup_preserves_metadata_when_directory_removal_fails(tmp_path):
    repository = LocalExecutionRepository(tmp_path)
    await repository.claim("child", "sampling", parent_job_id="root")
    work = tmp_path / "work"
    work.mkdir()
    await repository.prepare(
        cloud_job_id="child",
        request_hash="digest",
        options={},
        work_dir=work,
        request_path=work / "request.json",
        result_path=work / "result.json",
    )
    await repository.update("child", ExecutionState.FAILED)

    with patch(
        "oqtopus_engine_core.simulator.execution.local_execution_repository.shutil.rmtree",
        side_effect=PermissionError,
    ):
        with pytest.raises(PermissionError):
            await repository.cleanup_artifacts("child", tmp_path)
    assert (await repository.get("child")).state is ExecutionState.FAILED
    assert work.exists()
    assert await LocalExecutionRepository(tmp_path).cleanup_artifacts("child", tmp_path)
    assert await repository.get("child") is None


@pytest.mark.asyncio
async def test_cleanup_rejects_active_and_unsafe_child_directories(tmp_path):
    repository = LocalExecutionRepository(tmp_path / "work")
    await repository.claim("child", "sampling")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "work" / "link"
    link.symlink_to(outside)
    await repository.prepare(
        cloud_job_id="child",
        request_hash="digest",
        options={},
        work_dir=link,
        request_path=link / "request.json",
        result_path=link / "result.json",
    )
    with pytest.raises(RuntimeError, match="active execution"):
        await repository.cleanup_artifacts("child", tmp_path / "work")
    await repository.update("child", ExecutionState.FAILED)
    with pytest.raises(ValueError, match="unsafe"):
        await repository.cleanup_artifacts("child", tmp_path / "work")
    assert outside.exists()
    assert await repository.get("child") is not None
