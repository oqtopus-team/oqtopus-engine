import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Never

# ruff: noqa: DOC201, DOC501
from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    Step,
    StepResult,
)
from oqtopus_engine_core.simulator.execution import (
    ExecutionRecord,
    ExecutionRepository,
    ExecutionState,
    JobReader,
    LocalExecutionRepository,
)
from oqtopus_engine_core.simulator.mpi_qulacs import (
    QulacsExecutionRequest,
    SlurmSimulatorOptions,
    build_execution_request,
    build_slurm_job_labels,
    canonical_request_json,
    read_worker_result,
    request_hash,
)
from oqtopus_engine_core.simulator.scheduler import (
    SchedulerJobStatus,
    SchedulerState,
)
from oqtopus_engine_core.simulator.scheduler.slurm import (
    SlurmClient,
    SlurmReconciliationAmbiguousError,
    SlurmSubmissionUncertainError,
)

logger = logging.getLogger(__name__)


class JobCancelledError(RuntimeError):
    """Raised when SLURM cancellation has been confirmed."""


class SlurmCancellationPendingError(RuntimeError):
    """Raised when cancellation could not yet be sent or confirmed."""


def _raise_cancelled(message: str) -> None:
    raise JobCancelledError(message)


def _raise_runtime(message: str) -> Never:
    raise RuntimeError(message)


class SlurmSimulatorStep(Step):
    """Run MPI-Qulacs through a configured SLURM execution adapter."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        slurm_client: SlurmClient,
        execution_repository: ExecutionRepository,
        job_reader: JobReader,
        work_root: str,
        batch_script: str,
        worker_script: str,
        poll_interval_seconds: float = 5.0,
        qubits_per_node: int = 30,
        max_nodes: int = 1024,
        max_n_per_node: int = 48,
        max_timeout_seconds: int = 432000,
        max_shots: int = 100000,
        imaginary_tolerance: float = 1e-10,
    ) -> None:
        self._slurm_client = slurm_client
        self._execution_repository = execution_repository
        self._internal_execution_repository = LocalExecutionRepository(work_root)
        self._job_reader = job_reader
        self._work_root = Path(work_root).expanduser()
        self._batch_script = Path(batch_script).expanduser()
        self._worker_script = Path(worker_script).expanduser()
        self._poll_interval_seconds = poll_interval_seconds
        self._qubits_per_node = qubits_per_node
        self._max_nodes = max_nodes
        self._max_n_per_node = max_n_per_node
        self._max_timeout_seconds = max_timeout_seconds
        self._max_shots = max_shots
        self._imaginary_tolerance = imaginary_tolerance

    async def pre_process(  # noqa: C901, PLR0912, PLR0914, PLR0915
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> StepResult:
        """Submit or reattach one execution, then restore its validated result."""
        is_internal = job.parent is not None
        if job.job_type not in {"sampling", "estimation"}:
            message = f"unsupported SLURM simulator job type: {job.job_type}"
            raise ValueError(message)
        if any(bool(value) for value in job.mitigation_info.values()):
            message = "mitigation is unsupported by the SLURM simulator PoC"
            raise ValueError(message)

        execution_repository = (
            self._internal_execution_repository
            if is_internal
            else self._execution_repository
        )
        if is_internal:
            await execution_repository.initialize()
        record = await execution_repository.get(job.job_id)
        if record is None:
            if not is_internal:
                message = "root SLURM execution was not initialized"
                raise RuntimeError(message)
            await execution_repository.claim(job.job_id, job.job_type)
            record = await execution_repository.get(job.job_id)
        if record is None:  # pragma: no cover
            message = f"failed to claim SLURM execution: {job.job_id}"
            raise RuntimeError(message)

        work_dir = self._prepare_work_directory(job.job_id)
        request_path = work_dir / "request.json"
        result_path = work_dir / "result.json"
        self._validate_record_paths(record, work_dir, request_path, result_path)
        request_existed = request_path.is_file()
        request, options = self._resolve_request(job, record.options, request_path)
        digest = request_hash(request, options)
        record = await execution_repository.prepare(
            cloud_job_id=job.job_id,
            request_hash=digest,
            options=options.model_dump(),
            work_dir=work_dir,
            request_path=request_path,
            result_path=result_path,
        )
        self._write_atomic(request_path, canonical_request_json(request))
        recovered_submit_intent = (
            request_existed and record.state is ExecutionState.RUNNING
        )

        if record.state is ExecutionState.RESULT_READY:
            if not result_path.exists():
                _raise_runtime("validated SLURM result is missing")
            self._restore_result(job, request_path, result_path)
            return StepResult()

        if record.state is ExecutionState.SUCCEEDED:
            if not result_path.exists():
                _raise_runtime("completed SLURM result is missing")
            self._restore_result(job, request_path, result_path)
            return StepResult()

        if is_internal:
            if job.parent is None:  # pragma: no cover
                message = "internal SLURM job is missing its parent"
                raise RuntimeError(message)
            cloud_job_id = job.parent.job_id
        else:
            cloud_job_id = job.job_id
        cloud_job = await self._get_cloud_job_with_retry(
            cloud_job_id,
            None,
        )
        if cloud_job is not None and cloud_job.status == "cancelled":
            await execution_repository.update(
                job.job_id,
                ExecutionState.CANCELLED,
                expected={record.state},
                cloud_status=None if is_internal else "cancelled",
            )
            _raise_cancelled("job was cancelled before SLURM submission")
        if (
            cloud_job is not None
            and cloud_job.status == "cancelling"
            and not recovered_submit_intent
        ):
            await execution_repository.update(
                job.job_id,
                ExecutionState.CANCELLED,
                expected={record.state},
                cloud_status=None if is_internal else "cancelled",
            )
            _raise_cancelled("job was cancelled before SLURM submission")

        if record.state is ExecutionState.READY:
            if is_internal:
                record = await execution_repository.update(
                    job.job_id,
                    ExecutionState.RUNNING,
                    expected={ExecutionState.READY},
                )
            else:
                message = "root SLURM execution was not started"
                raise RuntimeError(message)
        job_name, comment = build_slurm_job_labels(job.job_id, digest)
        slurm_job_id = record.slurm_job_id
        if slurm_job_id is None:
            try:
                slurm_job_id = await self._slurm_client.find_job(
                    job_name=job_name,
                    start_time=datetime.fromisoformat(record.created_at)
                    - timedelta(days=1),
                )
            except SlurmReconciliationAmbiguousError:
                message = (
                    "multiple SLURM allocations match the persisted submit "
                    "intent; manual reconciliation is required"
                )
                _raise_runtime(message)
            except Exception as exc:
                message = "SLURM submission reconciliation is uncertain"
                raise SlurmSubmissionUncertainError(message) from exc
        if slurm_job_id is None:
            if recovered_submit_intent:
                message = (
                    "persisted SLURM submit intent has no unique allocation; "
                    "manual reconciliation is required"
                )
                _raise_runtime(message)
            slurm_job_id = await self._slurm_client.submit(
                job_name=job_name,
                comment=comment,
                work_dir=work_dir,
                batch_script=self._batch_script,
                worker_script=self._worker_script,
                nodes=options.n_nodes or 1,
                tasks_per_node=options.n_per_node,
                timeout_seconds=options.timeout_seconds,
            )
        await execution_repository.update(
            job.job_id,
            ExecutionState.RUNNING,
            expected={ExecutionState.RUNNING, ExecutionState.RESULT_READY},
            slurm_job_id=slurm_job_id,
        )

        await self._wait_for_result(
            job,
            slurm_job_id,
            request_path,
            result_path,
            execution_repository=execution_repository,
            cloud_job_id=cloud_job_id,
        )
        return StepResult()

    async def post_process(
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> StepResult:
        """Finalize only internal child execution metadata."""
        if job.parent is None:
            return StepResult()
        if job.result is None:
            message = "job result is None"
            raise ValueError(message)
        record = await self._internal_execution_repository.get(job.job_id)
        if record is not None and record.state is ExecutionState.RESULT_READY:
            await self._internal_execution_repository.update(
                job.job_id,
                ExecutionState.SUCCEEDED,
                expected={ExecutionState.RESULT_READY},
            )
        return StepResult()

    def _resolve_request(
        self,
        job: Job,
        recorded_options: dict[str, Any] | None,
        request_path: Path,
    ) -> tuple[QulacsExecutionRequest, SlurmSimulatorOptions]:
        if request_path.is_file():
            if recorded_options is None:
                message = "persisted SLURM request has no recorded options"
                raise ValueError(message)
            request = QulacsExecutionRequest.model_validate_json(
                request_path.read_text(encoding="utf-8")
            )
            options = SlurmSimulatorOptions.model_validate(recorded_options).resolve(
                n_qubits=request.n_qubits,
                qubits_per_node=self._qubits_per_node,
                max_nodes=self._max_nodes,
                max_n_per_node=self._max_n_per_node,
                max_timeout_seconds=self._max_timeout_seconds,
            )
            if request.job_type != job.job_type:
                message = "persisted SLURM request job_type does not match Cloud"
                raise ValueError(message)
            return request, options

        options = SlurmSimulatorOptions.model_validate(job.simulator_info)
        request = build_execution_request(job, options)
        options = options.resolve(
            n_qubits=request.n_qubits,
            qubits_per_node=self._qubits_per_node,
            max_nodes=self._max_nodes,
            max_n_per_node=self._max_n_per_node,
            max_timeout_seconds=self._max_timeout_seconds,
        )
        if request.job_type == "sampling" and (request.shots or 0) > self._max_shots:
            message = f"shots exceeds simulator limit {self._max_shots}"
            raise ValueError(message)
        return request.model_copy(update={"n_per_node": options.n_per_node}), options

    @staticmethod
    def _validate_record_paths(
        record: ExecutionRecord,
        work_dir: Path,
        request_path: Path,
        result_path: Path,
    ) -> None:
        expected = {
            "work_dir": work_dir,
            "request_path": request_path,
            "result_path": result_path,
        }
        for field, path in expected.items():
            recorded_path = getattr(record, field)
            if recorded_path is not None and Path(recorded_path) != path:
                message = f"persisted SLURM {field} does not match configured root"
                raise ValueError(message)

    async def _wait_for_result(  # noqa: C901, PLR0912, PLR0913
        self,
        job: Job,
        slurm_job_id: str | None,
        request_path: Path,
        result_path: Path,
        *,
        execution_repository: ExecutionRepository,
        cloud_job_id: str,
    ) -> None:
        if slurm_job_id is None:
            message = "execution record is missing its SLURM job ID"
            raise RuntimeError(message)

        while True:
            cloud_job = await self._get_cloud_job_with_retry(
                cloud_job_id,
                slurm_job_id,
            )
            cancellation_requested = (
                cloud_job is not None and cloud_job.status == "cancelling"
            )

            status = await self._get_slurm_status_with_retry(
                job.job_id,
                slurm_job_id,
            )
            if status is None:
                logger.warning(
                    "SLURM allocation is temporarily absent from queue and accounting",
                    extra={"job_id": job.job_id, "slurm_job_id": slurm_job_id},
                )
            elif status.state in {SchedulerState.PENDING, SchedulerState.RUNNING}:
                if cancellation_requested:
                    try:
                        await self._slurm_client.cancel(slurm_job_id)
                    except Exception as exc:
                        message = f"SLURM cancellation remains pending: {exc}"
                        raise SlurmCancellationPendingError(message) from exc
            elif status.state is SchedulerState.COMPLETED:
                if status.exit_code not in {None, "0:0"}:
                    message = f"SLURM job completed with exit code {status.exit_code}"
                    raise RuntimeError(message)
                self._restore_result(job, request_path, result_path)
                await execution_repository.update(
                    job.job_id,
                    ExecutionState.RESULT_READY,
                )
                return
            elif status.state is SchedulerState.CANCELLED:
                if await asyncio.to_thread(result_path.is_file):
                    self._restore_result(job, request_path, result_path)
                    await execution_repository.update(
                        job.job_id,
                        ExecutionState.RESULT_READY,
                    )
                    return
                if cancellation_requested:
                    message = "SLURM cancellation confirmed"
                else:
                    message = "SLURM allocation was cancelled independently of Cloud"
                raise JobCancelledError(message)
            else:
                message = (
                    f"SLURM job failed with state {status.raw_state}: "
                    f"{status.reason or 'no reason'}"
                )
                raise RuntimeError(message)
            await asyncio.sleep(self._poll_interval_seconds)

    async def _get_cloud_job_with_retry(
        self,
        job_id: str,
        slurm_job_id: str | None,
    ) -> Job | None:
        errors = 0
        while True:
            try:
                return await self._job_reader.get_job(job_id)
            except Exception:  # noqa: BLE001
                errors += 1
                logger.warning(
                    "Cloud status observation failed; SLURM polling will retry",
                    extra={
                        "job_id": job_id,
                        "slurm_job_id": slurm_job_id,
                        "consecutive_errors": errors,
                    },
                    exc_info=True,
                )
                await asyncio.sleep(self._poll_interval_seconds)

    async def _get_slurm_status_with_retry(
        self,
        job_id: str,
        slurm_job_id: str,
    ) -> SchedulerJobStatus | None:
        errors = 0
        while True:
            try:
                return await self._slurm_client.get_status(slurm_job_id)
            except Exception:  # noqa: BLE001
                errors += 1
                logger.warning(
                    "SLURM status observation failed; polling will retry",
                    extra={
                        "job_id": job_id,
                        "slurm_job_id": slurm_job_id,
                        "consecutive_errors": errors,
                    },
                    exc_info=True,
                )
                await asyncio.sleep(self._poll_interval_seconds)

    def _restore_result(
        self,
        job: Job,
        request_path: Path,
        result_path: Path,
    ) -> None:
        request = QulacsExecutionRequest.model_validate_json(
            request_path.read_text(encoding="utf-8")
        )
        job.result, job.execution_time = read_worker_result(
            result_path,
            request,
            imaginary_tolerance=self._imaginary_tolerance,
        )

    def _prepare_work_directory(self, job_id: str) -> Path:
        self._work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = self._work_root.resolve()
        token = hashlib.sha256(job_id.encode()).hexdigest()[:32]
        work_dir = root / token
        if work_dir.is_symlink():
            message = f"SLURM work directory must not be a symlink: {work_dir}"
            raise ValueError(message)
        work_dir.mkdir(mode=0o700, exist_ok=True)
        if work_dir.resolve().parent != root:
            message = "SLURM work directory escaped its configured root"
            raise ValueError(message)
        return work_dir

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        with temporary_path.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
