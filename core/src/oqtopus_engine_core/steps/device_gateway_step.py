import logging
import time

import grpc

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobResult,
    SamplingResult,
    Step,
)
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2, qpu_pb2_grpc

logger = logging.getLogger(__name__)


def _select_program(job: Job) -> str:
    transpile_result = job.job_info.transpile_result
    if transpile_result is None or transpile_result.transpiled_program is None:
        return job.job_info.program[0]
    return transpile_result.transpiled_program


class DeviceGatewayStep(Step):
    """Step that sends a job to the device gateway via gRPC during pre_process."""

    def __init__(self, gateway_address: str = "localhost:50051") -> None:
        self._channel = grpc.aio.insecure_channel(gateway_address)
        self._stub = qpu_pb2_grpc.QpuServiceStub(self._channel)
        logger.info(
            "DeviceGatewayStep was initialized with gateway_address=%s",
            gateway_address,
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job by sending a request to the device gateway.

        This method sends a gRPC request to the device gateway for job execution,
        and updates the job with the result.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            RuntimeError: If the device status is not available.

        """
        # Skip SSE job
        if job.job_type == "sse":
            logger.debug(
                "job_type is sse, skipping",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        start = time.perf_counter()

        # Update job status
        job.status = "running"
        # TODO nowait
        # await gctx.job_repository.update_job_status_nowait(job)
        await gctx.job_repository.update_job_status(job)

        # Check device status
        service_status = await self._stub.GetServiceStatus(
            qpu_pb2.GetServiceStatusRequest()
        )
        logger.info(
            "GetServiceStatus response",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "service_status": service_status.service_status,
            },
        )
        if service_status.service_status != qpu_pb2.ServiceStatus.SERVICE_STATUS_ACTIVE:
            message = "device status is not available"
            raise RuntimeError(message)

        # Call device gateway
        if job.job_type in {"sampling", "multi_manual"}:
            job_request = qpu_pb2.CallJobRequest(
                job_id=job.job_id,
                shots=job.shots,
                program=_select_program(job),
            )
            logger.info(
                "CallJob request",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "job_request": job_request,
                },
            )
            job_response = await self._stub.CallJob(job_request)
            if job_response.status != qpu_pb2.JobStatus.JOB_STATUS_SUCCESS:
                logger.error(
                    "failed to execute job on device gateway",
                    extra={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "job_response": job_response,
                    },
                )
                msg = "failed to execute job on device"
                raise RuntimeError(msg)
            logger.info(
                "CallJob response",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "job_response": job_response,
                },
            )
            execution_time = time.perf_counter() - start

            # Update job
            job.execution_time = float(f"{execution_time:.3f}")
            job.job_info.result = JobResult(
                sampling=SamplingResult(counts=job_response.result.counts)
            )
            job.job_info.message = job_response.result.message

        elif job.job_type == "estimation":
            jctx["estimation_job_info"].counts_list = []
            for program in jctx["estimation_job_info"].preprocessed_qasms:
                logger.debug("program: %s", program)
                job_request = qpu_pb2.CallJobRequest(
                    job_id=job.job_id,
                    shots=job.shots,
                    program=program,
                )
                logger.info(
                    "CallJob request",
                    extra={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "job_request": job_request,
                    },
                )
                job_response = await self._stub.CallJob(job_request)
                if job_response.status != qpu_pb2.JobStatus.JOB_STATUS_SUCCESS:
                    logger.error(
                        "failed to execute job on device gateway",
                        extra={
                            "job_id": job.job_id,
                            "job_type": job.job_type,
                            "job_response": job_response,
                        },
                    )
                    msg = "failed to execute job on device"
                    raise RuntimeError(msg)
                logger.info(
                    "CallJob response",
                    extra={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "job_response": job_response,
                    },
                )

                jctx["estimation_job_info"].counts_list.append(
                    job_response.result.counts
                )
            execution_time = time.perf_counter() - start

            # Update job
            job.execution_time = float(f"{execution_time:.3f}")
            job.job_info.message = job_response.result.message

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job by sending a request to the device gateway.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
