import json
import logging
import time

import grpc
from omegaconf import OmegaConf

from oqtopus_engine_core.framework import (
    EstimationResult,
    GlobalContext,
    Job,
    JobContext,
    JobResult,
    Step,
)
from oqtopus_engine_core.interfaces.estimator_interface.v1 import (
    estimator_pb2,
    estimator_pb2_grpc,
)

logger = logging.getLogger(__name__)
DEFAULT_BASIS_GATES = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]


class EstimationJobInfo:
    """Estimation job information model."""

    preprocessed_qasms: list[str] | None = None
    grouped_operators: list[list] | None = None
    counts_list: list[dict[str, int]] | None = None
    result: EstimationResult | None = None


class EstimatorStep(Step):
    """Handles the estimation workflow for quantum computations.

    This step is responsible for pre-processing quantum circuits before estimation
    and post-processing measurement results to calculate expectation values and
    standard deviations of quantum operators via gRPC communication with the
    estimator service.

    Attributes:
        estimator_address: Address of the gRPC estimator server.

    Methods:
        pre_process: Prepares quantum circuits and operators for estimation.
        post_process: Processes measurement results to calculate expectation values.

    """

    def __init__(
        self,
        estimator_address: str = "localhost:52012",
        basis_gates: list[str] | None = None,
    ) -> None:
        """Initialize the EstimatorStep.

        Args:
            estimator_address: Address of the gRPC estimator server.

        """
        self._channel = grpc.aio.insecure_channel(estimator_address)
        self._stub = estimator_pb2_grpc.EstimatorServiceStub(self._channel)
        if basis_gates is None:
            basis_gates_resolved = None
        elif OmegaConf.is_config(basis_gates):
            basis_gates_resolved = OmegaConf.to_container(basis_gates, resolve=True)
        else:
            basis_gates_resolved = list(basis_gates)
        self._basis_gates = basis_gates_resolved or list(DEFAULT_BASIS_GATES)
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
        """Pre-process the job by sending a request to estimator.

        This method prepares an estimation job by processing the QASM code and operators.
        It updates the job context with preprocessed QASMs and grouped operators needed for estimation.

        Notes:
            - For estimation job types, it extracts the QASM code from either the original program
              or the transpiled result.
            - It handles virtual-to-physical qubit mapping if available from transpilation.
            - The preprocessing groups operators and prepares QASMs for the estimation step.
            - Results are stored in the job context under 'estimation_job_info'.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            ValueError: If the operator is not specified in the job.

        """
        if job.job_type != "estimation":
            logger.debug(
                "job_type is not 'estimation', skipping pre_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        virtual_physical_mapping: dict[str, int] | None = None
        jctx["estimation_job_info"] = EstimationJobInfo()

        # Call estimator
        if job.job_info.transpile_result is None:
            qasm_code = job.job_info.program[0]
            virtual_physical_mapping = None
        else:
            qasm_code = job.job_info.transpile_result.transpiled_program
            virtual_physical_mapping = (
                job.job_info.transpile_result.virtual_physical_mapping["qubit_mapping"]
            )

        if job.job_info.operator is None:
            message = "the operator is not specified in the job."
            raise ValueError(message)

        # Convert operators to string format for gRPC
        operators_str = str([(op.pauli, op.coeff) for op in job.job_info.operator])

        # Prepare mapping list
        if virtual_physical_mapping is not None:
            sorted_vpm = sorted(
                virtual_physical_mapping.items(),
                key=lambda item: int(item[0]),
            )
            mapping_list = [item[1] for item in sorted_vpm]
        else:
            mapping_list = []

        # Call gRPC estimator pre-process
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
        pre_processed_qasms = list(response.qasm_codes)
        grouped_operators_json = response.grouped_operators

        # Parse grouped operators from JSON
        grouped_operators = json.loads(grouped_operators_json)

        # Update job
        jctx["estimation_job_info"].preprocessed_qasms = pre_processed_qasms
        jctx["estimation_job_info"].grouped_operators = grouped_operators

    async def post_process(
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job by sending a request to estimator.

        This method handles post-processing for estimation jobs by calculating the
        expectation values and standard deviations from measurement results. The
        calculated values are then stored in the job's result object.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        if job.job_type != "estimation":
            logger.debug(
                "job_type is not 'estimation', skipping post_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        if (
            job.job_info.result is not None
            and job.job_info.result.estimation is not None
            and job.job_info.result.estimation.exp_value is not None
        ):
            logger.info(
                "skip estimator post_process because estimation result is precomputed",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        logger.debug(
            "estimation_job_info: estimation_counts=%s, grouped_operators=%s",
            jctx["estimation_job_info"].counts_list,
            jctx["estimation_job_info"].grouped_operators,
            extra={"job_id": job.job_id, "job_type": job.job_type},
        )

        # Convert counts_list to protobuf format
        counts_pb_list = []
        for counts_dict in jctx["estimation_job_info"].counts_list:
            counts_pb = estimator_pb2.Counts(counts=counts_dict)
            counts_pb_list.append(counts_pb)

        # Convert grouped_operators to JSON string
        grouped_operators_json = json.dumps(
            jctx["estimation_job_info"].grouped_operators
        )

        # Call gRPC estimator post-process
        request = estimator_pb2.ReqEstimationPostProcessRequest(
            counts=counts_pb_list,
            grouped_operators=grouped_operators_json,
        )
        logger.info(
            "ReqEstimationPostProcess request",
            extra={"job_id": job.job_id, "job_type": job.job_type, "request": request},
        )

        start = time.perf_counter()
        response = await self._stub.ReqEstimationPostProcess(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "ReqEstimationPostProcess response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "job_id": job.job_id,
                "job_type": job.job_type,
                "response": response,
            },
        )
        expval = response.expval
        stds = response.stds

        # Update job
        if job.job_info.result is None:
            job.job_info.result = JobResult()
        if job.job_info.result.estimation is None:
            job.job_info.result.estimation = EstimationResult()
        job.job_info.result.estimation.exp_value = float(expval)
        job.job_info.result.estimation.stds = float(stds)
