import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oqtopus_engine_core.simulator import (
    CommandResult,
    SchedulerState,
    SlurmClient,
    SlurmReconciliationAmbiguousError,
    SlurmSubmissionUncertainError,
)


class StubRunner:
    def __init__(self, results: list[CommandResult]):
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    async def run(self, argv, *, timeout_seconds, cwd=None, env=None):
        self.calls.append(tuple(argv))
        return self.results.pop(0)


def command_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    return CommandResult(
        argv=("stub",),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.mark.asyncio
async def test_submit_uses_configured_scripts_and_parses_job_id():
    runner = StubRunner([command_result("12345;cluster\n")])
    client = SlurmClient(runner, partition="test-partition")

    job_id = await client.submit(
        job_name="oqtopus-abcd",
        comment="oqtopus:abcd:1234",
        work_dir=Path("/shared/jobs/abcd"),
        batch_script=Path("/opt/oqtopus/run.sh"),
        worker_script=Path("/opt/oqtopus/run_qulacs_mpi.py"),
        nodes=4,
        tasks_per_node=8,
        timeout_seconds=90,
    )

    assert job_id == "12345"
    assert runner.calls == [(
        "sbatch",
        "--parsable",
        "--partition=test-partition",
        "--nodes=4",
        "--ntasks=32",
        "--ntasks-per-node=8",
        "--time=00:01:30",
        "--job-name=oqtopus-abcd",
        "--comment=oqtopus:abcd:1234",
        "--chdir=/shared/jobs/abcd",
        "--output=/shared/jobs/abcd/stdout.log",
        "--error=/shared/jobs/abcd/stderr.log",
        "/opt/oqtopus/run.sh",
        "/opt/oqtopus/run_qulacs_mpi.py",
    )]


@pytest.mark.asyncio
async def test_submit_logs_command_and_redacted_script_contents(
    caplog: Any,
    tmp_path: Path,
) -> None:
    batch_script = tmp_path / "run.sh"
    batch_script.write_text(
        "#!/bin/sh\necho safe\nexport API_TOKEN=do-not-log\n",
        encoding="utf-8",
    )
    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        'TOKEN = "also-do-not-log"\nprint("worker")\n',
        encoding="utf-8",
    )
    runner = StubRunner([command_result("12345")])
    client = SlurmClient(
        runner,
        partition="test-partition",
        account="test-account",
        qos="test-qos",
    )
    caplog.set_level(
        logging.DEBUG,
        logger="oqtopus_engine_core.simulator.scheduler.slurm.client",
    )

    await client.submit(
        job_name="oqtopus-abcd",
        comment="oqtopus:abcd:1234",
        work_dir=tmp_path,
        batch_script=batch_script,
        worker_script=worker_script,
        nodes=1,
        tasks_per_node=2,
        timeout_seconds=90,
    )

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "SLURM submission command prepared"
    )
    assert "sbatch" in record.slurm_command
    assert "--nodes=1" in record.slurm_command
    assert "safe" in record.batch_script_content
    assert "worker" in record.worker_script_content
    assert "do-not-log" not in record.batch_script_content
    assert "also-do-not-log" not in record.worker_script_content
    assert "<redacted>" in record.batch_script_content
    assert "<redacted>" in record.worker_script_content


@pytest.mark.asyncio
async def test_submit_does_not_retry_uncertain_controller_failure():
    runner = StubRunner([
        command_result(
            stderr="Unable to contact slurm controller",
            returncode=1,
        ),
        command_result("12346"),
    ])
    client = SlurmClient(
        runner,
        partition="test-partition",
        controller_retry_count=2,
    )

    with pytest.raises(SlurmSubmissionUncertainError, match="reconcile by job name"):
        await client.submit(
            job_name="oqtopus-abcd",
            comment="oqtopus:abcd:1234",
            work_dir=Path("/shared/jobs/abcd"),
            batch_script=Path("/opt/oqtopus/run.sh"),
            worker_script=Path("/opt/oqtopus/run_qulacs_mpi.py"),
            nodes=4,
            tasks_per_node=8,
            timeout_seconds=90,
        )

    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_get_status_uses_sacct_when_job_left_queue():
    runner = StubRunner([
        command_result(),
        command_result("12345|COMPLETED|0:0|None\n"),
    ])
    client = SlurmClient(runner, partition="test-partition")

    status = await client.get_status("12345")

    assert status is not None
    assert status.state is SchedulerState.COMPLETED
    assert status.exit_code == "0:0"
    assert [call[0] for call in runner.calls] == ["squeue", "sacct"]


@pytest.mark.asyncio
async def test_find_job_uses_sacct_when_allocation_left_queue():
    job_name = "oqtopus-jobhash-reqhash"
    runner = StubRunner([
        command_result(),
        command_result(f"12345|{job_name}\n"),
    ])
    client = SlurmClient(runner, partition="test-partition")

    job_id = await client.find_job(
        job_name=job_name,
        start_time=datetime(2026, 8, 30, 12, 34, 56, tzinfo=UTC),
    )

    assert job_id == "12345"
    assert [call[0] for call in runner.calls] == ["squeue", "sacct"]
    assert "--format=%A|%j" in runner.calls[0]
    assert "--starttime=2026-08-30T12:34:56" in runner.calls[1]
    assert "--format=JobIDRaw,JobName" in runner.calls[1]


@pytest.mark.asyncio
async def test_find_job_requires_exact_job_name_match():
    job_name = "oqtopus-jobhash-reqhash"
    runner = StubRunner([
        command_result(f"12345|{job_name}-other\n"),
        command_result(f"12345|{job_name}-other\n"),
    ])
    client = SlurmClient(runner, partition="test-partition")

    assert await client.find_job(job_name=job_name) is None


@pytest.mark.asyncio
async def test_find_job_rejects_ambiguous_matches():
    job_name = "oqtopus-jobhash-reqhash"
    runner = StubRunner([
        command_result(f"12345|{job_name}\n12346|{job_name}\n"),
        command_result(),
    ])
    client = SlurmClient(runner, partition="test-partition")

    with pytest.raises(SlurmReconciliationAmbiguousError, match="multiple"):
        await client.find_job(
            job_name=job_name,
        )


@pytest.mark.asyncio
async def test_find_job_rejects_matches_split_across_queue_and_accounting():
    job_name = "oqtopus-jobhash-reqhash"
    runner = StubRunner([
        command_result(f"12345|{job_name}\n"),
        command_result(f"12346|{job_name}\n"),
    ])
    client = SlurmClient(runner, partition="test-partition")

    with pytest.raises(SlurmReconciliationAmbiguousError, match="multiple"):
        await client.find_job(
            job_name=job_name,
        )


@pytest.mark.asyncio
async def test_cancel_rejects_non_numeric_job_id():
    client = SlurmClient(StubRunner([]), partition="test-partition")

    with pytest.raises(ValueError, match="invalid SLURM job ID"):
        await client.cancel("123;touch")
