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

    def __init__(
        self,
        gateway_address: str = "localhost:50051",
    ) -> None:
        self._channel = grpc.aio.insecure_channel(gateway_address)
        self._stub = qpu_pb2_grpc.QpuServiceStub(self._channel)

        logger.info(
            "DeviceGatewayStep was initialized",
            extra={
                "gateway_address": gateway_address,
            },
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job by sending a request to the device gateway.

        This method sends gRPC requests to the device gateway for job execution,
        and updates the job with execution outputs.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            RuntimeError: If device status is unavailable or QPU execution fails.

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
            job_response = await self._call_job(
                job=job,
                program=_select_program(job),
                request_label="CallJob request",
                failure_message="failed to execute job on device",
            )
            execution_time = time.perf_counter() - start

            # Update job
            job.execution_time = float(f"{execution_time:.3f}")
            job.job_info.result = JobResult(
                sampling=SamplingResult(counts=job_response.result.counts)
            )
            job.job_info.message = job_response.result.message
            return

        if job.job_type != "estimation":
            return

        run_direct_estimation = True
        zne_job_info = jctx.get("zne_job_info")
        if zne_job_info and zne_job_info.get("execution_programs"):
            try:
                execution_programs = zne_job_info["execution_programs"]
                zne_programs = [item.program for item in execution_programs]
                zne_extras = [
                    {
                        "scale_factor": item.scale_factor,
                        "repetition": item.repetition,
                        "program_index": item.program_index,
                        "suffix": item.suffix,
                    }
                    for item in execution_programs
                ]
                responses = await self._call_job_batch(
                    job=job,
                    programs=zne_programs,
                    request_label="CallJob request for zne execution program",
                    failure_message="failed to execute zne program on device",
                    extras=zne_extras,
                )

                execution_results: list[dict[str, object]] = [
                    {
                        "scale_factor": float(item.scale_factor),
                        "repetition": int(item.repetition),
                        "program_index": int(item.program_index),
                        "counts": dict(response.result.counts),
                    }
                    for item, response in zip(execution_programs, responses, strict=True)
                ]
                if responses:
                    job.job_info.message = responses[-1].result.message

                zne_job_info["execution_results"] = execution_results
                logger.info(
                    "completed zne execution programs on device gateway",
                    extra={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "execution_results_count": len(execution_results),
                    },
                )
                execution_time = time.perf_counter() - start
                job.execution_time = float(f"{execution_time:.3f}")
                run_direct_estimation = False
            except Exception:
                if zne_job_info.get("fail_open", True):
                    logger.exception(
                        "zne execution failed, fallback to direct estimation due to fail_open",
                        extra={"job_id": job.job_id, "job_type": job.job_type},
                    )
                    zne_job_info["execution_results"] = []
                else:
                    raise

        if not run_direct_estimation:
            return

        # Fallback / non-ZNE path for estimation
        programs = list(jctx["estimation_job_info"].preprocessed_qasms)
        for program in programs:
            logger.debug("program: %s", program)
        responses = await self._call_job_batch(
            job=job,
            programs=programs,
            request_label="CallJob request",
            failure_message="failed to execute job on device",
        )
        jctx["estimation_job_info"].counts_list = [
            dict(response.result.counts) for response in responses
        ]

        # Update job
        execution_time = time.perf_counter() - start
        job.execution_time = float(f"{execution_time:.3f}")
        if responses:
            job.job_info.message = responses[-1].result.message

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

    async def _call_job(
        self,
        job: Job,
        program: str,
        request_label: str,
        failure_message: str,
        extra: dict[str, object] | None = None,
    ) -> qpu_pb2.CallJobResponse:
        job_request = qpu_pb2.CallJobRequest(
            job_id=job.job_id,
            shots=job.shots,
            program=program,
        )
        request_extra = {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "job_request": job_request,
        }
        if extra:
            request_extra.update(extra)
        logger.info(request_label, extra=request_extra)

        job_response = await self._stub.CallJob(job_request)
        if job_response.status != qpu_pb2.JobStatus.JOB_STATUS_SUCCESS:
            failure_extra = {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job_response": job_response,
            }
            if extra:
                failure_extra.update(extra)
            logger.error("failed to execute job on device gateway", extra=failure_extra)
            raise RuntimeError(failure_message)

        response_extra = {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "job_response": job_response,
        }
        if extra:
            response_extra.update(extra)
        logger.info("CallJob response", extra=response_extra)
        return job_response

    async def _call_job_batch(
        self,
        job: Job,
        programs: list[str],
        request_label: str,
        failure_message: str,
        extras: list[dict[str, object]] | None = None,
    ) -> list[qpu_pb2.CallJobResponse]:
        responses: list[qpu_pb2.CallJobResponse] = []
        for index, program in enumerate(programs):
            extra = extras[index] if extras and index < len(extras) else None
            response = await self._call_job(
                job=job,
                program=program,
                request_label=request_label,
                failure_message=failure_message,
                extra=extra,
            )
            responses.append(response)
        return responses
