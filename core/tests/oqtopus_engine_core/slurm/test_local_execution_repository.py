from pathlib import Path

import pytest

from oqtopus_engine_core.slurm import ExecutionState, LocalExecutionRepository


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
    assert persisted is not None
    assert persisted.artifact_retained is False
