import json
import logging
import time

import grpc

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext, Step
from oqtopus_engine_core.interfaces.combiner_interface.v1 import (
    combiner_pb2,
    combiner_pb2_grpc,
)

logger = logging.getLogger(__name__)


COMBINED_QUBITS_LIST_KEY = "combined_qubits_list"


def divide_string_by_lengths(input_str: str, lengths: list[int]) -> list[str]:
    """Split the input string into multiple strings based on given lengths.

    ex) input: "101011011", lengths: [2, 3, 4] -> ["10", "101", "1011"]

    Args:
        input_str (str): The input string to be divided.
        lengths (list[int]): A list of lengths for each segment.

    Returns:
        list[str]: A list of divided strings.

    Raises:
        ValueError: If the sum of lengths exceeds the length of input_str.

    """
    result = []
    current_pos = 0
    message_inconsistent_qubits = "inconsistent qubits"
    try:
        for length in lengths:
            result.append(input_str[current_pos : current_pos + length])
            current_pos += length

    except Exception as e:
        logger.exception(
            "failed to divide string by lengths",
            extra={"input_str": input_str, "lengths": lengths},
        )
        raise ValueError(message_inconsistent_qubits) from e

    if current_pos != len(input_str):
        logger.error(
            "failed to divide string by lengths",
            extra={"input_str": input_str, "lengths": lengths},
        )
        raise ValueError(message_inconsistent_qubits)

    return result


def divide_result(
    job: Job, jctx: dict,
) -> dict[int, dict[str, int]]:
    """Divide the job result into multiple results based on combined qubits list.

    Args:
        job (Job): The job object containing the result to be divided.
        jctx (dict): The job context containing the combined qubits list.

    Returns:
        dict[int, dict[str, int]]: A dictionary mapping circuit index to divided result.

    Raises:
        ValueError: If the job result counts are inconsistent.

    """
    if not job.job_info.result.sampling.counts:
        message = "inconsistent qubit property"
        logger.error(message, extra={"job_id": job.job_id})
        raise ValueError(message)
    combined_qubits_list = jctx.get(COMBINED_QUBITS_LIST_KEY, [])

    # Divide results
    divided_job_result: dict[int, dict[str, int]] = {}

    for key, value in job.job_info.result.sampling.counts.items():
        try:
            divided_keys = divide_string_by_lengths(key, combined_qubits_list)
            logger.debug(
                "divided_keys",
                extra={
                    "job_id": job.job_id,
                    "original_key": key,
                    "divided_keys": divided_keys,
                },
            )
        except ValueError:
            logger.exception(
                "failed to divide the result",
                extra={"job_id": job.job_id, "original_key": key},
            )
            raise

        for i, divided_one_key in enumerate(divided_keys):
            ith_circuit = len(combined_qubits_list) - i - 1
            if ith_circuit not in divided_job_result:
                divided_job_result[ith_circuit] = {}
            divided_job_result[ith_circuit][divided_one_key] = (
                divided_job_result[ith_circuit].get(divided_one_key, 0) + value
            )

    return divided_job_result


class MultiManualStep(Step):
    """Step that sends a command to the Combiner via gRPC during pre_process."""

    def __init__(self, combiner_address: str = "localhost:5002") -> None:
        self._channel = grpc.aio.insecure_channel(combiner_address)
        self._stub = combiner_pb2_grpc.CombinerServiceStub(self._channel)
        logger.info(
            "MultiManualStep was initialized",
            extra={"combiner_address": combiner_address},
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job to combine multiple circuits.

        This method prepares the job's QASM data, sends a gRPC request to the
        combiner for combining circuits, and updates the job with
        the combined QASM and qubits list.
        The qubits list will be used to divide the results in `post_process`.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            RuntimeError: If the CombineRequest fails or returns an error status.

        """
        if job.job_type != "multi_manual":
            logger.debug(
                "job_type is not 'multi_manual', skipping pre_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        device_info = json.loads(gctx.device.device_info)
        max_qubits = len(device_info["qubits"])

        # Call combiner
        qasm_array = json.dumps(job.job_info.program)
        qasm_array = qasm_array.replace("\\n", "")
        qasm_array = qasm_array.replace('\\"', '\\\\"')
        request = combiner_pb2.CombineRequest(
            qasm_array=qasm_array,
            max_qubits=max_qubits,
        )
        logger.info(
            "CombineRequest request",
            extra={"job_id": job.job_id, "job_type": job.job_type, "request": request},
        )

        start = time.perf_counter()
        response = await self._stub.Combine(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "CombineRequest response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "job_id": job.job_id,
                "job_type": job.job_type,
                "response": response,
            },
        )

        # When failed
        if response.combined_status != combiner_pb2.STATUS_SUCCESS:
            logger.error(
                "failed to combine programs",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "combined_status": combiner_pb2.Status.Name(
                        response.combined_status
                    ),
                },
            )
            # message to return to user
            if response.combined_status == combiner_pb2.STATUS_INVALID_QUBIT_SIZE:
                message = "failed to combine programs: invalid qubit size"
            else:
                message = "failed to combine programs"
            raise RuntimeError(message)

        # Update job object
        job.job_info.combined_program = response.combined_qasm
        jctx[COMBINED_QUBITS_LIST_KEY] = response.combined_qubits_list
        jctx["max_qubits"] = max_qubits
        jctx["combined_program"] = response.combined_qasm
        # Update job repository
        await gctx.job_repository.update_job_info_nowait(job)

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job to divide the results of multiple circuits.

        This method divides the job's result counts into multiple results
        based on the combined qubits list obtained during pre_process.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        if job.job_type != "multi_manual":
            logger.debug(
                "job_type is not 'multi_manual', skipping post_process",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        try:
            job.job_info.result.sampling.divided_counts = divide_result(job, jctx)
        except Exception:
            logger.exception(
                "failed to divide result",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            job.job_info.result.sampling.divided_counts = {}
