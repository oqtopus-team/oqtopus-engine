import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ruff: noqa: DOC201, DOC501
from .command_runner import CommandResult, CommandRunner
from .models import SlurmJobStatus, normalize_slurm_state
from .observability import (
    slurm_cancellation_counter,
    slurm_command_error_counter,
    slurm_submission_counter,
)

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SLURM_JOB_ID = re.compile(r"^[0-9]+$")
_CONTROLLER_FAILURE = "Unable to contact slurm controller"
_SQUEUE_FIELD_COUNT = 3
_SACCT_FIELD_COUNT = 4
_RECONCILIATION_FIELD_COUNT = 2


class SlurmCommandError(RuntimeError):
    """Raised when a SLURM command exits unsuccessfully or returns bad data."""


class SlurmReconciliationAmbiguousError(SlurmCommandError):
    """Raised when a submission key matches multiple SLURM allocations."""


class SlurmSubmissionUncertainError(RuntimeError):
    """Raised when sbatch may have accepted an allocation without a usable reply."""


def _format_time_limit(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if days:
        return f"{days}-{hours:02}:{minutes:02}:{remaining_seconds:02}"
    return f"{hours:02}:{minutes:02}:{remaining_seconds:02}"


class SlurmClient:
    """Typed async adapter for the SLURM command-line interface."""

    def __init__(  # noqa: PLR0913
        self,
        runner: CommandRunner | None = None,
        *,
        partition: str,
        account: str | None = None,
        qos: str | None = None,
        command_timeout_seconds: float = 30.0,
        controller_retry_count: int = 2,
        controller_retry_interval_seconds: float = 1.0,
        reconciliation_lookback_seconds: int = 86400,
    ) -> None:
        for label in (partition, account, qos):
            if label is not None and not _SAFE_LABEL.fullmatch(label):
                message = f"unsafe SLURM configuration label: {label!r}"
                raise ValueError(message)
        self._runner = runner or CommandRunner()
        self._partition = partition
        self._account = account
        self._qos = qos
        self._command_timeout_seconds = command_timeout_seconds
        self._controller_retry_count = controller_retry_count
        self._controller_retry_interval_seconds = controller_retry_interval_seconds
        self._reconciliation_lookback_seconds = reconciliation_lookback_seconds

    async def healthcheck(self) -> bool:
        """Return whether the configured partition is visible to SLURM."""
        result = await self._run((
            "sinfo",
            "--noheader",
            f"--partition={self._partition}",
            "--format=%P",
        ))
        return bool(result.stdout.strip())

    async def submit(  # noqa: PLR0913
        self,
        *,
        job_name: str,
        comment: str,
        work_dir: Path,
        batch_script: Path,
        worker_script: Path,
        nodes: int,
        tasks_per_node: int,
        timeout_seconds: int,
    ) -> str:
        """Submit a fixed batch script and return its numeric allocation ID."""
        for label in (job_name, comment):
            if not _SAFE_LABEL.fullmatch(label):
                message = f"unsafe SLURM job label: {label!r}"
                raise ValueError(message)
        if nodes < 1 or tasks_per_node < 1 or timeout_seconds < 1:
            message = "nodes, tasks_per_node, and timeout_seconds must be positive"
            raise ValueError(message)

        tasks = nodes * tasks_per_node
        argv = [
            "sbatch",
            "--parsable",
            f"--partition={self._partition}",
            f"--nodes={nodes}",
            f"--ntasks={tasks}",
            f"--ntasks-per-node={tasks_per_node}",
            f"--time={_format_time_limit(timeout_seconds)}",
            f"--job-name={job_name}",
            f"--comment={comment}",
            f"--chdir={work_dir}",
            f"--output={work_dir / 'stdout.log'}",
            f"--error={work_dir / 'stderr.log'}",
        ]
        if self._account is not None:
            argv.append(f"--account={self._account}")
        if self._qos is not None:
            argv.append(f"--qos={self._qos}")
        argv.extend((str(batch_script), str(worker_script)))

        try:
            result = await self._run(tuple(argv), retry_controller=False)
            job_id = result.stdout.strip().split(";", maxsplit=1)[0]
            self._validate_job_id(job_id)
        except Exception as exc:
            message = (
                "sbatch result is uncertain; reconcile by job name before retrying"
            )
            raise SlurmSubmissionUncertainError(message) from exc
        slurm_submission_counter.add(1)
        return job_id

    async def get_status(self, job_id: str) -> SlurmJobStatus | None:
        """Read queue status, falling back to accounting after queue eviction."""
        self._validate_job_id(job_id)
        queue_result = await self._run((
            "squeue",
            "--noheader",
            f"--jobs={job_id}",
            "--format=%i|%T|%r",
        ))
        queue_line = queue_result.stdout.strip().splitlines()
        if queue_line:
            fields = queue_line[0].split("|", maxsplit=2)
            if len(fields) != _SQUEUE_FIELD_COUNT:
                message = "unexpected squeue output"
                raise SlurmCommandError(message)
            return SlurmJobStatus(
                job_id=fields[0],
                state=normalize_slurm_state(fields[1]),
                raw_state=fields[1],
                reason=fields[2] or None,
            )

        accounting_result = await self._run((
            "sacct",
            "--noheader",
            "--allocations",
            f"--jobs={job_id}",
            "--format=JobIDRaw,State,ExitCode,Reason",
            "--parsable2",
        ))
        for line in accounting_result.stdout.splitlines():
            fields = [field.strip() for field in line.split("|")]
            if len(fields) >= _SACCT_FIELD_COUNT and fields[0] == job_id:
                return SlurmJobStatus(
                    job_id=job_id,
                    state=normalize_slurm_state(fields[1]),
                    raw_state=fields[1],
                    exit_code=fields[2] or None,
                    reason=fields[3] or None,
                )
        return None

    async def find_job(
        self,
        *,
        job_name: str,
        start_time: datetime | None = None,
    ) -> str | None:
        """Find an allocation submitted before its ID reached Cloud."""
        if not _SAFE_LABEL.fullmatch(job_name):
            message = f"unsafe SLURM job label: {job_name!r}"
            raise ValueError(message)

        queue_result = await self._run((
            "squeue",
            "--noheader",
            f"--name={job_name}",
            "--format=%A|%j",
        ))
        matches = self._matching_job_ids(queue_result.stdout, job_name)
        if start_time is None:
            start_time = datetime.now(UTC) - timedelta(
                seconds=self._reconciliation_lookback_seconds
            )
        elif start_time.tzinfo is None:
            message = "SLURM reconciliation start time must be timezone-aware"
            raise ValueError(message)
        start_time = start_time.astimezone(UTC)
        accounting_result = await self._run((
            "sacct",
            "--noheader",
            "--allocations",
            f"--name={job_name}",
            f"--starttime={start_time.strftime('%Y-%m-%dT%H:%M:%S')}",
            "--format=JobIDRaw,JobName",
            "--parsable2",
        ))
        matches.update(self._matching_job_ids(accounting_result.stdout, job_name))
        if len(matches) > 1:
            message = f"multiple SLURM allocations match job name {job_name}"
            raise SlurmReconciliationAmbiguousError(message)
        return next(iter(matches), None)

    async def cancel(self, job_id: str) -> None:
        """Cancel one allocation by numeric ID."""
        self._validate_job_id(job_id)
        await self._run(("scancel", "--", job_id))
        slurm_cancellation_counter.add(1)

    async def _run(
        self,
        argv: tuple[str, ...],
        *,
        retry_controller: bool = True,
    ) -> CommandResult:
        retry_count = self._controller_retry_count if retry_controller else 0
        for attempt in range(retry_count + 1):
            try:
                result = await self._runner.run(
                    argv,
                    timeout_seconds=self._command_timeout_seconds,
                )
            except Exception:
                slurm_command_error_counter.add(
                    1,
                    {"oqtopus.slurm.command": argv[0]},
                )
                raise
            if result.returncode == 0:
                return result
            if _CONTROLLER_FAILURE not in result.stderr or attempt >= retry_count:
                message = f"SLURM command failed: {argv[0]}: {result.stderr.strip()}"
                slurm_command_error_counter.add(
                    1,
                    {"oqtopus.slurm.command": argv[0]},
                )
                raise SlurmCommandError(message)
            await asyncio.sleep(self._controller_retry_interval_seconds)
        message = "unreachable SLURM retry state"
        raise AssertionError(message)

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not _SLURM_JOB_ID.fullmatch(job_id):
            message = f"invalid SLURM job ID: {job_id!r}"
            raise ValueError(message)

    @classmethod
    def _matching_job_ids(cls, output: str, job_name: str) -> set[str]:
        matches: set[str] = set()
        for line in output.splitlines():
            fields = [field.strip() for field in line.split("|", maxsplit=1)]
            if len(fields) == _RECONCILIATION_FIELD_COUNT and fields[1] == job_name:
                cls._validate_job_id(fields[0])
                matches.add(fields[0])
        return matches
