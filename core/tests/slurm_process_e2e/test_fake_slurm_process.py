import contextlib
import os
import signal
import subprocess  # noqa: S404
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from .process_support import read_json, replace_json, update_json

INTEGRATION_DIR = Path(__file__).parent
CORE_ROOT = INTEGRATION_DIR.parents[1]
WORKER = INTEGRATION_DIR / "fake_worker.py"
BATCH_SCRIPT = CORE_ROOT / "slurm_resources" / "run_qulacs_mpi_job.sh"


@dataclass
class FakeSlurmEnvironment:
    root: Path
    environment: dict[str, str]
    state_path: Path
    cloud_path: Path
    outcome_path: Path
    response_gate: Path
    worker_release: Path
    log_path: Path
    processes: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def start_harness(self) -> subprocess.Popen[bytes]:
        with self.log_path.open("ab") as log:
            process = subprocess.Popen(  # noqa: S603
                [
                    sys.executable,
                    "-m",
                    "slurm_process_e2e.process_harness",
                    "--process-lock",
                    str(self.root / "engine.lock"),
                    "--work-root",
                    str(self.root / "work"),
                    "--batch-script",
                    str(BATCH_SCRIPT),
                    "--worker-script",
                    str(WORKER),
                    "--cloud-state",
                    str(self.cloud_path),
                    "--outcome",
                    str(self.outcome_path),
                ],
                cwd=CORE_ROOT,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.processes.append(process)
        return process

    def state(self) -> dict[str, Any]:
        return read_json(self.state_path)

    def execution_state(self) -> str | None:
        return read_json(self.cloud_path).get("status")

    def request_cloud_cancellation(self) -> None:
        def update(state: dict[str, Any]) -> None:
            state["status"] = "cancelling"

        update_json(self.cloud_path, dict, update)

    def command_count(self, command: str, option: str | None = None) -> int:
        commands = self.state()["commands"]
        return sum(
            entry["command"] == command
            and (option is None or any(arg.startswith(option) for arg in entry["argv"]))
            for entry in commands
        )

    def assert_process_succeeds(self, process: subprocess.Popen[bytes]) -> None:
        try:
            return_code = process.wait(timeout=15)
        except subprocess.TimeoutExpired as error:
            pytest.fail(
                self.log_path.read_text(encoding="utf-8"),
                pytrace=False,
            )
            raise AssertionError from error
        assert return_code == 0, self.log_path.read_text(encoding="utf-8")


def _wrapper(command: str) -> str:
    return (
        "#!/bin/sh\n"
        'exec "$FAKE_SLURM_PYTHON" -m '
        "slurm_process_e2e.fake_slurm "
        f'"{command}" "$@"\n'
    )


@pytest.fixture
def fake_slurm(tmp_path: Path) -> Iterator[FakeSlurmEnvironment]:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    for command in ("sinfo", "sbatch", "squeue", "sacct", "scancel", "srun"):
        executable = binary_dir / command
        executable.write_text(_wrapper(command), encoding="utf-8")
        executable.chmod(0o755)

    state_path = tmp_path / "scheduler" / "state.json"
    cloud_path = tmp_path / "cloud.json"
    state_path.parent.mkdir()
    replace_json(
        state_path,
        {
            "next_job_id": 12345,
            "submit_count": 0,
            "cancel_count": 0,
            "commands": [],
            "jobs": {},
        },
    )
    replace_json(cloud_path, {"status": "ready"})
    response_gate = tmp_path / "release-sbatch-response"
    worker_release = tmp_path / "release-worker"
    environment = os.environ.copy()
    environment.update({
        "PATH": f"{binary_dir}{os.pathsep}{environment['PATH']}",
        "PYTHONPATH": os.pathsep.join((
            str(CORE_ROOT / "src"),
            str(CORE_ROOT / "tests"),
            environment.get("PYTHONPATH", ""),
        )),
        "FAKE_SLURM_PYTHON": sys.executable,
        "FAKE_SLURM_STATE_DIR": str(state_path.parent),
        "FAKE_SLURM_SBATCH_RESPONSE_GATE": str(response_gate),
        "FAKE_SLURM_WORKER_RELEASE": str(worker_release),
        "OQTOPUS_WORKER_PYTHON": sys.executable,
    })
    instance = FakeSlurmEnvironment(
        root=tmp_path,
        environment=environment,
        state_path=state_path,
        cloud_path=cloud_path,
        outcome_path=tmp_path / "outcome.json",
        response_gate=response_gate,
        worker_release=worker_release,
        log_path=tmp_path / "harness.log",
    )
    yield instance

    response_gate.touch()
    worker_release.touch()
    for process in instance.processes:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    state = instance.state()
    for job in state["jobs"].values():
        allocation_pid = job.get("allocation_pid")
        if allocation_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(allocation_pid, signal.SIGTERM)
        submitter_pid = job.get("submitter_pid")
        if submitter_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(submitter_pid, signal.SIGTERM)


def _wait_until(predicate: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError


def _terminate(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    process.wait(timeout=5)


def test_process_executes_production_launcher_and_accounting_fallback(
    fake_slurm: FakeSlurmEnvironment,
) -> None:
    fake_slurm.response_gate.touch()
    fake_slurm.worker_release.touch()

    process = fake_slurm.start_harness()
    fake_slurm.assert_process_succeeds(process)

    outcome = read_json(fake_slurm.outcome_path)
    assert outcome["status"] == "succeeded"
    assert outcome["execution_state"] == "succeeded"
    assert outcome["result"]["sampling"]["counts"] == {"0": 10}
    state = fake_slurm.state()
    assert state["submit_count"] == 1
    assert fake_slurm.command_count("sinfo") == 1
    assert fake_slurm.command_count("sbatch", "--ntasks=1") == 1
    assert fake_slurm.command_count("sbatch", "--ntasks-per-node=1") == 1
    assert fake_slurm.command_count("srun", "--ntasks-per-node=1") == 1
    assert fake_slurm.command_count("sacct", "--jobs=") >= 1


def test_process_restart_reattaches_completed_allocation(
    fake_slurm: FakeSlurmEnvironment,
) -> None:
    fake_slurm.response_gate.touch()
    first = fake_slurm.start_harness()
    _wait_until(lambda: fake_slurm.state()["submit_count"] == 1)
    _terminate(first)

    fake_slurm.worker_release.touch()
    _wait_until(
        lambda: next(iter(fake_slurm.state()["jobs"].values()))["state"]
        == "COMPLETED"
    )
    second = fake_slurm.start_harness()
    fake_slurm.assert_process_succeeds(second)

    assert fake_slurm.state()["submit_count"] == 1
    assert fake_slurm.execution_state() == "succeeded"
    assert fake_slurm.command_count("sacct", "--name=") >= 1


def test_uncertain_sbatch_response_reattaches_by_job_name(
    fake_slurm: FakeSlurmEnvironment,
) -> None:
    first = fake_slurm.start_harness()
    _wait_until(lambda: fake_slurm.state()["submit_count"] == 1)
    _wait_until(lambda: fake_slurm.execution_state() == "running")
    _terminate(first)

    baseline = fake_slurm.command_count("squeue", "--name=")
    second = fake_slurm.start_harness()
    _wait_until(lambda: fake_slurm.command_count("squeue", "--name=") > baseline)
    fake_slurm.response_gate.touch()
    fake_slurm.worker_release.touch()
    fake_slurm.assert_process_succeeds(second)

    assert fake_slurm.state()["submit_count"] == 1
    assert fake_slurm.execution_state() == "succeeded"


def test_cloud_cancellation_stops_allocation(
    fake_slurm: FakeSlurmEnvironment,
) -> None:
    fake_slurm.response_gate.touch()
    process = fake_slurm.start_harness()
    _wait_until(lambda: fake_slurm.state()["submit_count"] == 1)

    fake_slurm.request_cloud_cancellation()
    fake_slurm.assert_process_succeeds(process)

    outcome = read_json(fake_slurm.outcome_path)
    state = fake_slurm.state()
    assert outcome["status"] == "cancelled"
    assert outcome["execution_state"] == "cancelled"
    assert read_json(fake_slurm.cloud_path)["status"] == "cancelled"
    assert state["cancel_count"] == 1
    assert next(iter(state["jobs"].values()))["state"] == "CANCELLED"
