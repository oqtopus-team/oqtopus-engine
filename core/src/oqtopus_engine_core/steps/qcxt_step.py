import json
import logging
import time
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import grpc  # type: ignore[import-untyped]

from oqtopus_engine_core.framework import (
    EstimationResult,
    GlobalContext,
    Job,
    JobContext,
    JobResult,
    MitigationDetails,
    PipelineDirective,
    QcxtDetails,
    Step,
    StepResult,
    mark_job_terminal,
)
from oqtopus_engine_core.framework.model import TranspileResult
from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2,
    mitigator_pb2_grpc,
)

logger = logging.getLogger(__name__)

QCXT_JOIN_INFO_KEY = "qcxt_join_info"
QCXT_CHILD_INDEX_KEY = "qcxt_child_index"
QCXT_STEP_NAME = "QcxtStep"


class QcxtJoinInfo:
    """Metadata stored on the parent context for qCxT joins."""

    coefficients: list[float] | None = None
    child_order: list[str] | None = None
    started_at: float | None = None


def _build_child_job(
    parent_job: Job,
    *,
    child_job_id: str,
    program: str,
    transpile_result: TranspileResult,
) -> Job:
    """Create an internal sampling child job for a single measurement circuit.

    Returns:
        A sampling child job derived from the estimation parent job with qCxt function.

    """
    child_transpile_result = deepcopy(transpile_result) if transpile_result else None
    if child_transpile_result and transpile_result.transpiled_program:
        # Update the transpiled program to the child's specific circuit
        child_transpile_result.transpiled_program = program
        # Update the stats to empty dictionary
        child_transpile_result.stats = {}

    child_transpiler_info = deepcopy(parent_job.transpiler_info)
    if child_transpiler_info:
        child_transpiler_options = child_transpiler_info.get("transpiler_options", {})
        if child_transpiler_options.get("initial_layout", None) is not None:
            # Update the initial layout to None in order to disable mp_auto
            child_transpiler_info["transpiler_options"]["initial_layout"] = None

    return Job(
        job_id=child_job_id,
        # The child has no Cloud record of its own; repository updates for
        # it resolve to the parent (see `resolve_repository_jobs`).
        repository_job_id=parent_job.repository_job_id,
        name=parent_job.name,
        description=parent_job.description,
        device_id=parent_job.device_id,
        shots=parent_job.shots,
        job_type="estimation",
        input=parent_job.input,
        program=[program],
        operator=parent_job.operator,
        transpile_result=child_transpile_result,
        result=JobResult(estimation=EstimationResult()),
        transpiler_info=child_transpiler_info,
        simulator_info=deepcopy(parent_job.simulator_info),
        mitigation_info=deepcopy(parent_job.mitigation_info),
        status=parent_job.status,
        message=parent_job.message,
        submitted_at=parent_job.submitted_at,
        ready_at=parent_job.ready_at,
        running_at=parent_job.running_at,
    )


class QcxtStep(Step):
    """Split estimation jobs in pre-process and join them in post-process."""

    def __init__(
        self,
        mitigator_address: str = "localhost:51011",
        grpc_options: Sequence[tuple[str, Any]] | None = None,
        basis_gates: list[str] | None = None,
        *,
        enable_qcxt: bool = False,
    ) -> None:
        self._channel = grpc.aio.insecure_channel(
            mitigator_address,
            options=grpc_options,
        )
        self._stub = mitigator_pb2_grpc.QcxtServiceStub(self._channel)
        self._basis_gates = basis_gates
        self._enable_qcxt = enable_qcxt
        logger.info(
            "QcxtStep was initialized",
            extra={
                "mitigator_address": mitigator_address,
                "basis_gates": basis_gates,
                "grpc_options": grpc_options,
            },
        )

    def _should_skip_pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> tuple[bool, str | None]:
        """Check if pre_process should be skipped.

        Returns:
            (should_skip, message): Should skip and optional message

        Raises:
            ValueError: If qcxt is set while qCxT is disabled.

        """
        if not self._enable_qcxt:
            if job.mitigation_info.get("qcxt", False) is True:
                message = "qcxt is set, but qCxT is disabled."
                raise ValueError(message)
            return True, "qCxT is disabled, skipping pre_process."

        if gctx.device is None:  # pragma: no cover
            message = "gctx.device is None."
            raise ValueError(message)
        if gctx.device.device_info is None:  # pragma: no cover
            message = "gctx.device.device_info is None."
            raise ValueError(message)

        if job.program is None or len(job.program) == 0:  # pragma: no cover
            message = "job.program is None or empty."
            raise ValueError(message)

        if job.job_type != "estimation":
            return True, "job_type is not 'estimation', skipping pre_process."

        if job.mitigation_info.get("qcxt", False) is False:
            return True, "qcxt is not designated, skipping pre_process."

        if QCXT_CHILD_INDEX_KEY in jctx:
            return True, "qCxT child skips pre_process body."

        return False, None

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        """Split an estimation job into child jobs during pre-process.

        This method ('qCxT') mitigates errors in an estimation job by gate cutting.
        The pre_process step splits the parent job into child jobs by swapping cx gates
        with Pauli gates. The child jobs are then processed separately and recombined
        to form the final result.

        Returns:
            StepResult: SPLIT_FOR_JOIN with child jobs for qcxt parents;
                NONE for all other cases.

        Raises:
            ValueError: If pre-process fails or returns inconsistent results.

        """
        should_skip, message = self._should_skip_pre_process(gctx, jctx, job)
        if should_skip:
            logger.debug(
                message,
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return StepResult()

        if job.transpile_result:
            program = job.transpile_result.transpiled_program
        else:
            program = job.program[0]  # type: ignore[index]
        request = mitigator_pb2.ReqQcxtPreProcessRequest(  # type: ignore[attr-defined]
            program=program,
            device_info=gctx.device.device_info,  # type: ignore[union-attr]
            shots=job.shots,
            basis_gates=self._basis_gates,
        )
        logger.info(
            "ReqQcxtPreProcess request",
            extra={"job_id": job.job_id, "job_type": job.job_type, "request": request},
        )

        start = time.perf_counter()
        response = await self._stub.ReqQcxtPreProcess(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "ReqQcxtPreProcess response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "job_id": job.job_id,
                "job_type": job.job_type,
                "response": response,
            },
        )
        if response.sub_programs is None:
            message = "No sub_programs returned from ReqQcxtPreProcess."
            raise ValueError(message)
        if len(response.sub_programs) != len(response.coefficients):
            message = "Mismatch between sub_programs and coefficients"
            raise ValueError(message)

        if job.result is None:
            job.result = JobResult()
        if job.result.mitigation_details is None:
            job.result.mitigation_details = MitigationDetails()
        if job.result.mitigation_details.qcxt is None:
            job.result.mitigation_details.qcxt = QcxtDetails()
        job.result.mitigation_details.qcxt.virtual_couplings = [
            json.loads(vc) for vc in response.virtual_couplings
        ]

        join_info = QcxtJoinInfo()
        join_info.coefficients = response.coefficients
        join_info.started_at = time.perf_counter()

        child_jobs: list[Job] = []
        child_ctxs: list[JobContext] = []
        child_order: list[str] = []
        for i, sub_program in enumerate(response.sub_programs):
            child_job_id = f"{job.job_id}-qcxt-{i}"
            child_jobs.append(
                _build_child_job(
                    job,
                    child_job_id=child_job_id,
                    program=sub_program,
                    transpile_result=job.transpile_result,  # type: ignore[arg-type]
                )
            )
            child_ctxs.append(
                JobContext(
                    initial={
                        QCXT_CHILD_INDEX_KEY: len(child_order),
                    }
                )
            )
            child_order.append(child_job_id)

        join_info.child_order = child_order
        jctx[QCXT_JOIN_INFO_KEY] = join_info

        return StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_jobs=child_jobs,
            child_contexts=child_ctxs,
        )

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        """Signal JOIN for qcxt split children; NONE for all other jobs.

        Returns:
            StepResult: JOIN directive for split children; NONE otherwise.

        """
        if QCXT_CHILD_INDEX_KEY in jctx:
            logger.debug(
                "qcxt split child reaching join point",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            # This child has no job repository record of its own (see
            # `_build_child_job`) and, once JOIN is signaled, the framework
            # never processes it again locally — mark it here or it stays
            # "ready"/"running" forever.
            mark_job_terminal(job, "succeeded")
            return StepResult(directive=PipelineDirective.JOIN)

        logger.debug(
            "non-child job, skipping join gate post_process",
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )
        return StepResult()

    async def join_jobs(
        self,
        gctx: GlobalContext,  # noqa: ARG002
        parent_jctx: JobContext,
        parent_job: Job,
        last_child: Job,
    ) -> None:
        """Aggregate child results into the parent estimation result.

        Raises:
            RuntimeError: If join metadata is missing.

        """
        join_info: QcxtJoinInfo | None = parent_jctx.get(QCXT_JOIN_INFO_KEY)
        if join_info is None:
            message = "qcxt_join_info is not initialized"
            raise RuntimeError(message)

        child_order = join_info.child_order or [
            child.job_id for child in parent_job.children
        ]
        child_by_id = {child.job_id: child for child in parent_job.children}

        sub_expvals_list = []
        sub_stds_list = []
        for child_id in child_order:
            child = child_by_id.get(child_id)
            if child is None or child.result is None or child.result.estimation is None:
                message = f"child estimation result not found during join: {child_id}"
                raise RuntimeError(message)
            sub_expvals_list.append(child.result.estimation.exp_value)
            sub_stds_list.append(child.result.estimation.stds)

        if join_info is None or join_info.coefficients is None:
            message = "qcxt_join_info is not initialized"
            raise RuntimeError(message)

        request = mitigator_pb2.ReqQcxtPostProcessRequest(  # type: ignore[attr-defined]
            sub_expvals=sub_expvals_list,
            sub_stds=sub_stds_list,
            coefficients=join_info.coefficients,
        )
        logger.info(
            "ReqQcxtPostProcess request",
            extra={
                "job_id": parent_job.job_id,
                "job_type": parent_job.job_type,
                "last_child_job_id": last_child.job_id,
                "request": request,
            },
        )

        start = time.perf_counter()
        response = await self._stub.ReqQcxtPostProcess(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "ReqQcxtPostProcess response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "job_id": parent_job.job_id,
                "job_type": parent_job.job_type,
                "response": response,
            },
        )

        if parent_job.result is None:
            parent_job.result = JobResult()
        if parent_job.result.estimation is None:
            parent_job.result.estimation = EstimationResult()
        parent_job.result.estimation.exp_value = float(response.exp_val)
        parent_job.result.estimation.stds = float(response.stds)
        parent_job.execution_time = float(
            f"{sum(child.execution_time or 0.0 for child in parent_job.children):.3f}"
        )
        parent_job.message = last_child.message
