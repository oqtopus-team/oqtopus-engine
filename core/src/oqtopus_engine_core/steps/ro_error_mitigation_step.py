import json
import logging
import time
from collections.abc import Sequence
from typing import Any, Protocol

import grpc  # type: ignore[import-untyped]

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    SamplingResult,
    Step,
    StepResult,
)
from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2,
    mitigator_pb2_grpc,
)
from oqtopus_engine_core.steps.estimator_step import (
    ESTIMATION_EXPECTATION_VALUES_KEY,
    ESTIMATION_PAULIS_KEY,
    ESTIMATION_STANDARD_DEVIATION_UPPER_BOUNDS_KEY,
)

logger = logging.getLogger(__name__)


class _ExpectationValueMitigationResponse(Protocol):
    @property
    def expectation_values(self) -> Sequence[float]: ...

    @property
    def standard_deviation_upper_bounds(self) -> Sequence[float]: ...


def _apply_expectation_value_mitigation_response(
    jctx: JobContext,
    paulis: Sequence[str],
    response: _ExpectationValueMitigationResponse,
) -> None:
    expectation_values = list(response.expectation_values)
    standard_deviation_upper_bounds = list(response.standard_deviation_upper_bounds)
    if not (
        len(expectation_values) == len(standard_deviation_upper_bounds) == len(paulis)
    ):
        message = (
            "mitigator response expectation values, standard-deviation upper "
            "bounds, and Pauli labels must have equal lengths"
        )
        raise RuntimeError(message)

    jctx[ESTIMATION_EXPECTATION_VALUES_KEY] = expectation_values
    jctx[ESTIMATION_STANDARD_DEVIATION_UPPER_BOUNDS_KEY] = (
        standard_deviation_upper_bounds
    )
    logger.debug(
        "Readout-error-mitigated expectation values",
        extra={
            "expectation_values": expectation_values,
            "standard_deviation_upper_bounds": standard_deviation_upper_bounds,
        },
    )


def _require_sampling_result(job: Job) -> SamplingResult:
    if job.result is None:  # pragma: no cover
        message = "job.result is None. Cannot perform readout error mitigation."
        raise ValueError(message)
    if job.result.sampling is None:  # pragma: no cover
        message = (
            "job.result.sampling is None. Cannot perform readout error mitigation."
        )
        raise ValueError(message)
    if job.result.sampling.counts is None:  # pragma: no cover
        message = (
            "job.result.sampling.counts is None. "
            "Cannot perform readout error mitigation."
        )
        raise ValueError(message)
    return job.result.sampling


class ReadoutErrorMitigationStep(Step):
    """Handles the readout error mitigation workflow for quantum computations via gRPC.

    This step communicates with a gRPC mitigator service to apply readout error
    mitigation to measurement results. It delegates all mitigation computation
    to the external mitigator service.

    Attributes:
        mitigator_address: Address of the gRPC mitigator service.

    Methods:
        pre_process: Placeholder that performs no operation for this step.
        post_process: Sends measurement data to mitigator service via gRPC.

    """

    def __init__(
        self,
        mitigator_address: str,
        grpc_options: Sequence[tuple[str, Any]] | None = None,
    ) -> None:
        """Initialize the ReadoutErrorMitigationStep with mitigator service address.

        Args:
            mitigator_address: Address of the gRPC mitigator service
                (e.g., "localhost:52011").
            grpc_options: gRPC channel options.

        """
        self._channel = grpc.aio.insecure_channel(
            mitigator_address,
            options=grpc_options,
        )
        self._stub = mitigator_pb2_grpc.MitigatorServiceStub(self._channel)
        logger.info(
            "ReadoutErrorMitigationStep was initialized",
            extra={
                "mitigator_address": mitigator_address,
                "grpc_options": grpc_options,
            },
        )

    async def pre_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,  # noqa: ARG002
        job: Job,  # noqa: ARG002
    ) -> StepResult:
        """Pre-process the job before error mitigation.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        return StepResult()

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        """Post-process the job by sending a request to mitigator service via gRPC.

        This method handles post-processing for mitigation jobs by sending
        sampling measurement results to the gRPC mitigator service. The
        mitigated counts are then stored in the job's result object.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            ValueError: If gctx.device is None, gctx.device.device_info is None,
                or required job result fields are None.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        if (
            job.mitigation_info == {}
            or job.mitigation_info.get("ro_error_mitigation") is None
        ):
            logger.debug(
                "ro_error_mitigation is not set, skipping post_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return StepResult()

        if job.mitigation_info["ro_error_mitigation"] == "pseudo_inverse":
            # Extract necessary information from the job
            if gctx.device is None:  # pragma: no cover
                message = (
                    "gctx.device is None. Cannot perform readout error mitigation."
                )
                raise ValueError(message)
            if gctx.device.device_info is None:  # pragma: no cover
                message = (
                    "gctx.device.device_info is None. "
                    "Cannot perform readout error mitigation."
                )
                raise ValueError(message)
            device_info_json = json.loads(gctx.device.device_info)

            # Prepare device_topology protobuf (common for both job types)
            qubits_pb = []
            for qubit in device_info_json["qubits"]:
                mes_error = mitigator_pb2.MesError(  # type: ignore[attr-defined]
                    p0m1=float(qubit["meas_error"]["prob_meas1_prep0"]),
                    p1m0=float(qubit["meas_error"]["prob_meas0_prep1"]),
                )
                qubit_pb = mitigator_pb2.Qubit(mes_error=mes_error)  # type: ignore[attr-defined]
                qubits_pb.append(qubit_pb)

            device_topology = mitigator_pb2.DeviceTopology(qubits=qubits_pb)  # type: ignore[attr-defined]

            if job.job_type not in {"sampling", "multi_manual"}:
                logger.debug(
                    "job_type is not 'sampling' or 'multi_manual', skipping mitigation",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
                return StepResult()

            sampling = _require_sampling_result(job)
            orig_counts = sampling.counts
            paulis: list[str] = jctx.get(ESTIMATION_PAULIS_KEY, [])

            if paulis:
                request = mitigator_pb2.ReqExpectationValueMitigationRequest(  # type: ignore[attr-defined]
                    device_topology=device_topology,
                    counts=orig_counts,
                    program=job.program[0],  # type: ignore[index]
                    paulis=paulis,
                )
                logger.info(
                    "ReqExpectationValueMitigation request",
                    extra={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "request": request,
                    },
                )
                start = time.perf_counter()
                response = await self._stub.ReqExpectationValueMitigation(request)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                logger.info(
                    "ReqExpectationValueMitigation response",
                    extra={
                        "elapsed_ms": round(elapsed_ms, 3),
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "response": response,
                    },
                )
                _apply_expectation_value_mitigation_response(
                    jctx=jctx,
                    paulis=paulis,
                    response=response,
                )
            else:
                request = mitigator_pb2.ReqMitigationRequest(  # type: ignore[attr-defined]
                    device_topology=device_topology,
                    counts=orig_counts,
                    program=job.program[0],  # type: ignore[index]
                )
                logger.info(
                    "ReqMitigation request",
                    extra={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "request": request,
                    },
                )
                start = time.perf_counter()
                response = await self._stub.ReqMitigation(request)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                logger.info(
                    "ReqMitigation response",
                    extra={
                        "elapsed_ms": round(elapsed_ms, 3),
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "response": response,
                    },
                )
                mitigated_counts = dict(response.counts)
                sampling.counts = mitigated_counts
                logger.debug(
                    "Readout-error-mitigated counts",
                    extra={
                        "mitigated_counts": mitigated_counts,
                        "original_counts": orig_counts,
                    },
                )

            return StepResult()
        return StepResult()
