import json
import logging
import time

import grpc

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    Step,
    TranspileResult,
)
from oqtopus_engine_core.interfaces.tranqu_server.proto.v1 import (
    tranqu_pb2,
    tranqu_pb2_grpc,
)

logger = logging.getLogger(__name__)


class TranquStep(Step):
    """Step that sends a command to the Tranqu Server via gRPC during pre_process."""

    def __init__(
        self,
        tranqu_address: str = "localhost:52020",
        default_transpiler_info: dict | None = None,
    ) -> None:
        if default_transpiler_info is None:
            default_transpiler_info = {}
        self._channel = grpc.aio.insecure_channel(tranqu_address)
        self._stub = tranqu_pb2_grpc.TranspilerServiceStub(self._channel)
        self._default_transpiler_info = default_transpiler_info
        logger.info(
            "TranquStep was initialized",
            extra={
                "tranqu_address": tranqu_address,
                "default_transpiler_info": default_transpiler_info,
            },
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job by sending a transpile request to the Tranqu Server.

        This method prepares the job's transpiler information, sends a gRPC request
        to the Tranqu Server for transpilation, and updates the job with the
        transpile result.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            ValueError: If gctx.device or gctx.device.device_info is None.

        """
        # Skip SSE job
        if job.job_type == "sse":
            logger.debug(
                "job_type is sse, skipping",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return
        # Skip if the transpiler is disabled
        if job.transpiler_info.get("transpiler_lib", {}) is None:
            logger.debug(
                "transpiler_lib is None, skipping",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        # Check device_info
        if gctx.device is None or gctx.device.device_info is None:
            message = "gctx.device or gctx.device.device_info is None"
            logger.error(
                message,
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            raise ValueError(message)

        # default_transpiler_info
        if job.transpiler_info == {}:
            job.transpiler_info = self._default_transpiler_info
            jctx["use_default_transpiler_info"] = True
            logger.debug(
                "use default transpiler_info: %s",
                job.transpiler_info,
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            # Update job repository
            await gctx.job_repository.update_job_transpiler_info_nowait(job)

        # Call tranqu
        if job.job_type == "multi_manual":
            program_to_transpile = job.job_info.combined_program
        else:
            program_to_transpile = job.job_info.program[0]
        request = tranqu_pb2.TranspileRequest(
            request_id="id",
            program=program_to_transpile,
            program_lib="openqasm3",
            transpiler_lib=job.transpiler_info["transpiler_lib"],
            transpiler_options=json.dumps(job.transpiler_info["transpiler_options"]),
            device=gctx.device.device_info,
            device_lib="oqtopus",
        )

        logger.info(
            "Transpile request",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
            },
        )

        start = time.perf_counter()
        response = await self._stub.Transpile(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "Transpile response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "job_id": job.job_id,
                "job_type": job.job_type,
                "response": response,
            },
        )

        # Update job object
        job.job_info.transpile_result = TranspileResult(
            transpiled_program=response.transpiled_program,
            stats=json.loads(response.stats),
            virtual_physical_mapping=json.loads(response.virtual_physical_mapping),
        )

        # Update job repository
        await gctx.job_repository.update_job_info_nowait(job)

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job after transpilation.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
