import json
import logging
import time

import grpc

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    Step,
)
from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2,
    mitigator_pb2_grpc,
)

logger = logging.getLogger(__name__)


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
        mitigator_address: str = "localhost:52011",
        mitigator_timeout_seconds: float = 120.0,
        zne_default_config: dict | None = None,
    ) -> None:
        """Initialize the ReadoutErrorMitigationStep with mitigator service address.

        Args:
            mitigator_address: Address of the gRPC mitigator service
                (e.g., "localhost:52011").
            mitigator_timeout_seconds: Backward-compatible parameter.
            zne_default_config: Backward-compatible parameter.

        """
        self._channel = grpc.aio.insecure_channel(mitigator_address)
        self._stub = mitigator_pb2_grpc.MitigatorServiceStub(self._channel)
        self._mitigator_timeout_seconds = mitigator_timeout_seconds
        logger.info(
            "ReadoutErrorMitigationStep was initialized",
            extra={
                "mitigator_address": mitigator_address,
                "mitigator_timeout_seconds": mitigator_timeout_seconds,
                "zne_default_config": zne_default_config,
            },
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job before error mitigation.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job by sending a request to mitigator service via gRPC.

        This method handles post-processing for mitigation jobs by sending measurement
        results to the gRPC mitigator service. The mitigated counts are then stored
        in the job's result object.

        For estimation jobs, it processes each count in counts_list.
        For sampling jobs, it processes the single counts result.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            ValueError: If gctx.device is None, gctx.device.device_info is None,
                or required job result fields are None.

        """
        if (
            job.mitigation_info == {}
            or job.mitigation_info.get("ro_error_mitigation") is None
        ):
            logger.debug(
                "ro_error_mitigation is not set, skipping post_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

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
                mes_error = mitigator_pb2.MesError(
                    p0m1=float(qubit["meas_error"]["prob_meas1_prep0"]),
                    p1m0=float(qubit["meas_error"]["prob_meas0_prep1"]),
                )
                qubit_pb = mitigator_pb2.Qubit(mes_error=mes_error)
                qubits_pb.append(qubit_pb)

            device_topology = mitigator_pb2.DeviceTopology(qubits=qubits_pb)

            # Process based on job type
            if job.job_type == "sampling":
                # For sampling jobs, process single counts result
                if job.job_info.result is None:  # pragma: no cover
                    message = (
                        "job.job_info.result is None. "
                        "Cannot perform readout error mitigation."
                    )
                    raise ValueError(message)
                if job.job_info.result.sampling is None:  # pragma: no cover
                    message = (
                        "job.job_info.result.sampling is None. "
                        "Cannot perform readout error mitigation."
                    )
                    raise ValueError(message)
                if job.job_info.result.sampling.counts is None:  # pragma: no cover
                    message = (
                        "job.job_info.result.sampling.counts is None. "
                        "Cannot perform readout error mitigation."
                    )
                    raise ValueError(message)
                orig_counts = job.job_info.result.sampling.counts

                # Call gRPC mitigator service
                request = mitigator_pb2.ReqMitigationRequest(
                    device_topology=device_topology,
                    counts=orig_counts,
                    program=job.job_info.program[0],
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

                # Update the job's result with mitigated counts
                job.job_info.result.sampling.counts = mitigated_counts
                logger.debug(
                    "ro_error_mitigated_counts is %s, original_counts is %s",
                    mitigated_counts,
                    orig_counts,
                )

            elif job.job_type == "estimation":
                # For estimation jobs, process each count in counts_list.
                if "estimation_job_info" not in jctx:
                    logger.warning(
                        "estimation_job_info not found in jctx for estimation job"
                    )
                    return

                estimation_job_info = jctx["estimation_job_info"]
                if estimation_job_info.counts_list is not None:
                    preprocessed_qasms = estimation_job_info.preprocessed_qasms
                    mitigated_counts_list = []

                    for index, orig_counts in enumerate(estimation_job_info.counts_list):
                        # Get corresponding QASM program
                        program = (
                            preprocessed_qasms[index]
                            if preprocessed_qasms and index < len(preprocessed_qasms)
                            else job.job_info.program[0]
                        )

                        # Prepare request
                        request = mitigator_pb2.ReqMitigationRequest(
                            device_topology=device_topology,
                            counts=orig_counts,
                            program=program,
                        )

                        # Call gRPC
                        logger.info(
                            "ReqMitigation request",
                            extra={
                                "job_id": job.job_id,
                                "job_type": job.job_type,
                                "request": request,
                            },
                        )
                        response = await self._stub.ReqMitigation(request)
                        logger.info(
                            "ReqMitigation response",
                            extra={
                                "job_id": job.job_id,
                                "job_type": job.job_type,
                                "response": response,
                            },
                        )
                        mitigated_counts = dict(response.counts)
                        mitigated_counts_list.append(mitigated_counts)

                        logger.debug(
                            "estimation[%d] "
                            "ro_error_mitigated_counts is %s, "
                            "original_counts is %s",
                            index,
                            mitigated_counts,
                            orig_counts,
                        )

                    # Update counts_list with mitigated results
                    estimation_job_info.counts_list = mitigated_counts_list
                    return

                # If direct estimation counts are absent, apply REM to ZNE execution results.
                zne_job_info = jctx.get("zne_job_info")
                if not zne_job_info:
                    logger.warning("counts_list is None in estimation_job_info")
                    return
                execution_results = zne_job_info.get("execution_results") or []
                execution_programs = zne_job_info.get("execution_programs") or []
                if len(execution_results) == 0 or len(execution_programs) == 0:
                    logger.warning("zne execution results/programs are missing for REM")
                    return

                program_map = {
                    (
                        float(item.scale_factor),
                        int(item.repetition),
                        int(item.program_index),
                    ): item.program
                    for item in execution_programs
                }
                mitigated_execution_results: list[dict[str, object]] = []
                for result in execution_results:
                    key = (
                        float(result["scale_factor"]),
                        int(result["repetition"]),
                        int(result["program_index"]),
                    )
                    program = program_map.get(key)
                    if program is None:
                        logger.warning("program is not found for zne execution result key=%s", key)
                        continue

                    request = mitigator_pb2.ReqMitigationRequest(
                        device_topology=device_topology,
                        counts=dict(result["counts"]),
                        program=program,
                    )
                    logger.info(
                        "ReqMitigation request for zne execution result",
                        extra={
                            "job_id": job.job_id,
                            "job_type": job.job_type,
                            "request": request,
                        },
                    )
                    response = await self._stub.ReqMitigation(request)
                    logger.info(
                        "ReqMitigation response for zne execution result",
                        extra={
                            "job_id": job.job_id,
                            "job_type": job.job_type,
                            "response": response,
                        },
                    )
                    mitigated_execution_results.append(
                        {
                            "scale_factor": float(result["scale_factor"]),
                            "repetition": int(result["repetition"]),
                            "program_index": int(result["program_index"]),
                            "counts": dict(response.counts),
                        }
                    )
                zne_job_info["execution_results"] = mitigated_execution_results

            return
