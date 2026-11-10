import contextlib
import os
import signal
import subprocess  # noqa: S404
import sys
import time
from pathlib import Path
from typing import Any

from .process_support import read_json, update_json


def _write_line(value: str) -> None:
    sys.stdout.write(f"{value}\n")
    sys.stdout.flush()


def _state_path() -> Path:
    return Path(os.environ["FAKE_SLURM_STATE_DIR"]) / "state.json"


def _new_state() -> dict[str, Any]:
    return {
        "next_job_id": 12345,
        "submit_count": 0,
        "cancel_count": 0,
        "commands": [],
        "jobs": {},
    }


def _record(command: str, argv: list[str]) -> None:
    def update(state: dict[str, Any]) -> None:
        state["commands"].append({
            "command": command,
            "argv": argv,
            "pid": os.getpid(),
        })

    update_json(_state_path(), _new_state, update)


def _option(argv: list[str], name: str) -> str | None:
    prefix = f"--{name}="
    return next(
        (argument[len(prefix) :] for argument in argv if argument.startswith(prefix)),
        None,
    )


def _submit(argv: list[str]) -> int:
    positional = [argument for argument in argv if not argument.startswith("--")]
    batch_script, worker_script = positional[-2:]

    def register(state: dict[str, Any]) -> str:
        job_id = str(state["next_job_id"])
        state["next_job_id"] += 1
        state["submit_count"] += 1
        state["jobs"][job_id] = {
            "job_id": job_id,
            "name": _option(argv, "job-name"),
            "comment": _option(argv, "comment"),
            "work_dir": _option(argv, "chdir"),
            "stdout_path": _option(argv, "output"),
            "stderr_path": _option(argv, "error"),
            "batch_script": batch_script,
            "worker_script": worker_script,
            "state": "PENDING",
            "reason": "Resources",
            "exit_code": None,
            "allocation_pid": None,
            "submitter_pid": os.getpid(),
        }
        return job_id

    job_id = update_json(_state_path(), _new_state, register)
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "slurm_process_e2e.fake_slurm",
            "allocation",
            job_id,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )

    def record_pid(state: dict[str, Any]) -> None:
        state["jobs"][job_id]["allocation_pid"] = process.pid

    update_json(_state_path(), _new_state, record_pid)
    gate_value = os.environ.get("FAKE_SLURM_SBATCH_RESPONSE_GATE")
    if gate_value is not None:
        gate = Path(gate_value)
        while not gate.exists():
            time.sleep(0.02)
    _write_line(f"{job_id};fake-cluster")
    return 0


def _allocation(job_id: str) -> int:
    def mark_running(state: dict[str, Any]) -> dict[str, Any] | None:
        job = state["jobs"][job_id]
        if job["state"] == "CANCELLED":
            return None
        job["state"] = "RUNNING"
        job["reason"] = "None"
        return dict(job)

    job = update_json(_state_path(), _new_state, mark_running)
    if job is None:
        return 0

    stdout_path = Path(job["stdout_path"])
    stderr_path = Path(job["stderr_path"])
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        stdout_path.open("ab") as stdout,
        stderr_path.open("ab") as stderr,
    ):
        result = subprocess.run(  # noqa: S603
            [job["batch_script"], job["worker_script"]],
            cwd=job["work_dir"],
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )

    def finish(state: dict[str, Any]) -> None:
        current = state["jobs"][job_id]
        if current["state"] == "CANCELLED":
            return
        if result.returncode == 0:
            current["state"] = "COMPLETED"
            current["exit_code"] = "0:0"
            current["reason"] = "None"
        else:
            current["state"] = "FAILED"
            current["exit_code"] = f"{result.returncode}:0"
            current["reason"] = "NonZeroExitCode"

    update_json(_state_path(), _new_state, finish)
    return result.returncode


def _queue(argv: list[str]) -> int:
    state = read_json(_state_path())
    job_id = _option(argv, "jobs")
    job_name = _option(argv, "name")
    jobs = state["jobs"].values()
    active = [job for job in jobs if job["state"] in {"PENDING", "RUNNING"}]
    if job_id is not None:
        active = [job for job in active if job["job_id"] == job_id]
        for job in active:
            _write_line(f"{job['job_id']}|{job['state']}|{job['reason']}")
    elif job_name is not None:
        active = [job for job in active if job["name"] == job_name]
        for job in active:
            _write_line(f"{job['job_id']}|{job['name']}")
    return 0


def _accounting(argv: list[str]) -> int:
    state = read_json(_state_path())
    job_id = _option(argv, "jobs")
    job_name = _option(argv, "name")
    jobs = list(state["jobs"].values())
    if job_id is not None:
        jobs = [job for job in jobs if job["job_id"] == job_id]
        for job in jobs:
            if job["state"] not in {"PENDING", "RUNNING"}:
                _write_line(
                    f"{job['job_id']}|{job['state']}|"
                    f"{job['exit_code'] or ''}|{job['reason']}"
                )
    elif job_name is not None:
        jobs = [job for job in jobs if job["name"] == job_name]
        for job in jobs:
            _write_line(f"{job['job_id']}|{job['name']}")
    return 0


def _cancel(argv: list[str]) -> int:
    job_id = argv[-1]

    def cancel(state: dict[str, Any]) -> int | None:
        job = state["jobs"].get(job_id)
        if job is None:
            return None
        state["cancel_count"] += 1
        job["state"] = "CANCELLED"
        job["reason"] = "CancelledByUser"
        job["exit_code"] = "0:15"
        return job["allocation_pid"]

    allocation_pid = update_json(_state_path(), _new_state, cancel)
    if allocation_pid is not None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(allocation_pid, signal.SIGTERM)
    return 0


def _run(argv: list[str]) -> int:
    command = list(argv)
    while command and command[0].startswith("--"):
        command.pop(0)
    if not command:
        return 2
    os.execvp(command[0], command)  # noqa: S606
    return 127


def _info(_argv: list[str]) -> int:
    _write_line(os.environ.get("SLURM_PARTITION", "fake"))
    return 0


def main() -> int:
    command = sys.argv[1]
    argv = sys.argv[2:]
    _record(command, argv)
    handlers = {
        "sinfo": _info,
        "sbatch": _submit,
        "squeue": _queue,
        "sacct": _accounting,
        "scancel": _cancel,
        "srun": _run,
        "allocation": lambda arguments: _allocation(arguments[0]),
    }
    handler = handlers.get(command)
    return handler(argv) if handler is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
