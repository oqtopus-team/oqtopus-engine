import json
import logging
import time
from copy import deepcopy

import grpc

from oqtopus_engine_core.framework import (
    EstimationResult,
    GlobalContext,
    HAS_ACTUAL_CHILDREN_KEY,
    Job,
    JobContext,
    JobInfo,
    JobResult,
    JoinOnPostprocess,
    SamplingResult,
    SplitOnPreprocess,
    Step,
)
from oqtopus_engine_core.interfaces.estimator_interface.v1 import (
    estimator_pb2,
    estimator_pb2_grpc,
)

logger = logging.getLogger(__name__)

ESTIMATION_JOIN_INFO_KEY = "estimation_join_info"
ESTIMATION_CHILD_INDEX_KEY = "estimation_child_index"


class EstimationJoinInfo:
    """Metadata stored on the parent context for estimation joins."""

    grouped_operators: list[list] | None = None
    child_order: list[str] | None = None
    started_at: float | None = None
    internal_children: bool = True


def _is_split_child_context(jctx: JobContext) -> bool:
    """Return True only for contexts explicitly marked as split children."""
    return (
        HAS_ACTUAL_CHILDREN_KEY in jctx
        and not jctx.get(HAS_ACTUAL_CHILDREN_KEY, False)
    )


def _build_estimator_request_payload(job: Job) -> tuple[str, list[int]]:
    """Build the base QASM and mapping list for estimator preprocess."""
    if job.job_info.transpile_result is None:
        return job.job_info.program[0], []

    transpile_result = job.job_info.transpile_result
    virtual_physical_mapping = transpile_result.virtual_physical_mapping["qubit_mapping"]
    sorted_vpm = sorted(
        virtual_physical_mapping.items(),
        key=lambda item: int(item[0]),
    )
    mapping_list = [item[1] for item in sorted_vpm]
    return transpile_result.transpiled_program, mapping_list


def _build_child_job(parent_job: Job, *, child_job_id: str, program: str) -> Job:
    """Create an internal sampling child job for a single measurement circuit."""
    return Job(
        job_id=child_job_id,
        name=parent_job.name,
        description=parent_job.description,
        device_id=parent_job.device_id,
        shots=parent_job.shots,
        job_type="sampling",
        job_info=JobInfo(
            program=[program],
            result=JobResult(sampling=SamplingResult()),
            message=parent_job.job_info.message,
        ),
        transpiler_info=deepcopy(parent_job.transpiler_info),
        simulator_info=deepcopy(parent_job.simulator_info),
        mitigation_info=deepcopy(parent_job.mitigation_info),
        status=parent_job.status,
        submitted_at=parent_job.submitted_at,
        ready_at=parent_job.ready_at,
        running_at=parent_job.running_at,
    )


class EstimatorStep(Step, SplitOnPreprocess, JoinOnPostprocess):
    """Split estimation jobs in pre-process and join them in post-process."""

    def __init__(
        self,
        estimator_address: str = "localhost:52012",
        basis_gates: list[str] | None = None,
    ) -> None:
        self._channel = grpc.aio.insecure_channel(estimator_address)
        self._stub = estimator_pb2_grpc.EstimatorServiceStub(self._channel)
        self._basis_gates = basis_gates
        logger.info(
            "EstimatorStep was initialized",
            extra={
                "estimator_address": estimator_address,
                "basis_gates": self._basis_gates,
            },
        )

    async def pre_process(
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        if job.job_type != "estimation":
            logger.debug(
                "job_type is not 'estimation', skipping pre_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        if _is_split_child_context(jctx):
            logger.debug(
                "estimation child skips pre_process body",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        if job.job_info.operator is None:
            message = "the operator is not specified in the job."
            raise ValueError(message)

        qasm_code, mapping_list = _build_estimator_request_payload(job)
        operators_str = str([(op.pauli, op.coeff) for op in job.job_info.operator])
        request = estimator_pb2.ReqEstimationPreProcessRequest(
            qasm_code=qasm_code,
            operators=operators_str,
            basis_gates=self._basis_gates,
            mapping_list=mapping_list,
        )
        logger.info(
            "ReqEstimationPreProcess request",
            extra={"job_id": job.job_id, "job_type": job.job_type, "request": request},
        )

        start = time.perf_counter()
        response = await self._stub.ReqEstimationPreProcess(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "ReqEstimationPreProcess response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "job_id": job.job_id,
                "job_type": job.job_type,
                "response": response,
            },
        )

        join_info = EstimationJoinInfo()
        join_info.grouped_operators = json.loads(response.grouped_operators)
        join_info.started_at = time.perf_counter()

        child_jobs: list[Job] = []
        child_ctxs: list[JobContext] = []
        child_order: list[str] = []
        for index, program in enumerate(response.qasm_codes):
            child_job_id = f"{job.job_id}-estimation-child-{index}"
            child_jobs.append(
                _build_child_job(job, child_job_id=child_job_id, program=program)
            )
            child_ctxs.append(
                JobContext(
                    initial={
                        HAS_ACTUAL_CHILDREN_KEY: False,
                        ESTIMATION_CHILD_INDEX_KEY: index,
                    }
                )
            )
            child_order.append(child_job_id)

        join_info.child_order = child_order
        jctx[ESTIMATION_JOIN_INFO_KEY] = join_info
        job.children = child_jobs
        jctx.children = child_ctxs

    async def post_process(
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        if job.job_type != "estimation":
            logger.debug(
                "job_type is not 'estimation', skipping post_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        if not _is_split_child_context(jctx):
            logger.debug(
                "parent estimation job skips join gate post_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

    async def join_jobs(
        self,
        gctx: GlobalContext,  # noqa: ARG002
        parent_jctx: JobContext,
        parent_job: Job,
        last_child: Job,
    ) -> None:
        join_info: EstimationJoinInfo | None = parent_jctx.get(ESTIMATION_JOIN_INFO_KEY)
        if join_info is None or join_info.grouped_operators is None:
            message = "estimation_join_info is not initialized"
            raise RuntimeError(message)

        child_order = join_info.child_order or [child.job_id for child in parent_job.children]
        child_by_id = {child.job_id: child for child in parent_job.children}

        counts_pb_list = []
        for child_id in child_order:
            child = child_by_id.get(child_id)
            if child is None:
                message = f"child job not found during join: {child_id}"
                raise RuntimeError(message)
            sampling = child.job_info.result.sampling if child.job_info.result else None
            counts = sampling.counts if sampling else None
            if counts is None:
                message = f"child job counts are missing during join: {child_id}"
                raise RuntimeError(message)
            counts_pb_list.append(estimator_pb2.Counts(counts=counts))

        request = estimator_pb2.ReqEstimationPostProcessRequest(
            counts=counts_pb_list,
            grouped_operators=json.dumps(join_info.grouped_operators),
        )
        logger.info(
            "ReqEstimationPostProcess request",
            extra={
                "job_id": parent_job.job_id,
                "job_type": parent_job.job_type,
                "last_child_job_id": last_child.job_id,
                "request": request,
            },
        )

        start = time.perf_counter()
        response = await self._stub.ReqEstimationPostProcess(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "ReqEstimationPostProcess response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "job_id": parent_job.job_id,
                "job_type": parent_job.job_type,
                "response": response,
            },
        )

        if parent_job.job_info.result is None:
            parent_job.job_info.result = JobResult()
        if parent_job.job_info.result.estimation is None:
            parent_job.job_info.result.estimation = EstimationResult()
        parent_job.job_info.result.estimation.exp_value = float(response.expval)
        parent_job.job_info.result.estimation.stds = float(response.stds)
        if join_info.started_at is not None:
            parent_job.execution_time = float(
                f"{time.perf_counter() - join_info.started_at:.3f}"
            )
        parent_job.job_info.message = last_child.job_info.message
