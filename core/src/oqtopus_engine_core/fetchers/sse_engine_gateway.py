import asyncio
import json
import logging
import time

import grpc

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext, JobFetcher
from oqtopus_engine_core.framework.pipeline import PipelineExecutor
from oqtopus_engine_core.interfaces.sse_interface.v1 import (
    sse_pb2,
    sse_pb2_grpc,
)

logger = logging.getLogger(__name__)

PIPELINE_TIMEOUT_SECONDS_DEFAULT = 60  # 1 minute


class SseEngineGateway(JobFetcher):
    """Receive jobs via gRPC and feed them into the sse-engine pipeline."""

    def __init__(
        self,
        sse_engine_address: str = "[::]:5005",
    ) -> None:
        """Initialize the job gateway with the address:port to listen.

        Args:
            sse_engine_address: The address:port to listen on.

        """
        super().__init__()
        self._sse_engine_address = sse_engine_address

        logger.info(
            "SseEngineGateway was initialized",
            extra={"sse_engine_address": sse_engine_address},
        )

    async def start_server(self) -> None:
        """Start the gRPC server to receive jobs from SSE.

        Raises:
            ValueError: If pipeline or global context is not set.

        """
        if self.pipeline is None or self.gctx is None:
            msg = "PipelineExecutor and GlobalContext must not be None"
            raise ValueError(msg)

        server = grpc.aio.server()
        sse_servicer = SseEngineGatewayServicer(self.pipeline, self.gctx)
        sse_pb2_grpc.add_SseEngineServiceServicer_to_server(
            sse_servicer,
            server
        )
        server.add_insecure_port(self._sse_engine_address)
        await server.start()
        logger.info(
            "SseEngineGateway gRPC server started",
            extra={"sse_engine_address": self._sse_engine_address}
        )
        await server.wait_for_termination()

    async def start(self) -> None:
        """Start gRPC server to receive jobs and feed them into the pipeline."""
        await self.start_server()
        logger.info("SseEngineGateway was started")


class SseEngineGatewayServicer:
    """gRPC Servicer that handles job requests."""

    def __init__(self, pipeline: PipelineExecutor, gctx: GlobalContext) -> None:
        """Initialize the SseEngineGateway Servicer."""
        self.pipeline = pipeline
        self.gctx = gctx

    async def SseEngine(  # noqa: N802
        self,
        request: sse_pb2.SseEngineRequest,
        context: grpc.RpcContext,   # noqa: ARG002
    ) -> sse_pb2.SseEngineResponse:
        """Handle gRPC requests for executing pipeline.

        Args:
            request: The gRPC request containing job data.
            context: The gRPC context.

        Returns:
            sse_pb2.SseEngineResponse: The gRPC response.

        """
        # Extract job information from the request
        logger.info("received gRPC request of transpiling and executing QPU")
        logger.debug(
            "received request",
            extra={"request": request}
        )
        start = time.perf_counter()

        # Placeholder response structure
        res = sse_pb2.SseEngineResponse(
            status="failed",
            message="",
            job_json="",
        )

        # Convert gRPC request to Job object
        try:
            job = self._get_job_from_request(request)
        except ValueError:
            res.status = "failed"
            res.message = "invalid request data"
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.exception(
                "invalid request data",
                extra={
                    "elapsed_ms": round(elapsed_ms, 3),
                    "request": request,
                    "response": res,
                },
            )
            return res

        try:
            jctx = JobContext()
            await self.pipeline.execute_pipeline(self.gctx, jctx, job)
            # set timeout to prevent hanging
            # and wait for job completion
            async with asyncio.timeout(PIPELINE_TIMEOUT_SECONDS_DEFAULT):
                while job.status not in {"failed", "succeeded", "cancelled"}:
                    asyncio.Event().set()
                    await asyncio.sleep(0.1)
        except TimeoutError:
            logger.exception(
                "pipeline execution timed out",
                extra={"job_id": job.job_id}
            )
            res.status = "failed"
            res.message = "pipeline execution timed out"
        except Exception:
            logger.exception(
                "error during pipeline execution",
                extra={"job_id": job.job_id}
            )
            res.status = "failed"
            res.message = "error during pipeline execution"
        else:
            res.status = "succeeded"
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            res.job_json = job.model_dump_json()
            logger.info(
                "sse-engine pipeline execution finished",
                extra={
                    "elapsed_ms": round(elapsed_ms, 3),
                    "job_id": job.job_id,
                    "status": job.status,
                    "response": res
                },
            )
        return res

    @staticmethod
    def _get_job_from_request(request: sse_pb2.SseEngineRequest) -> Job:
        """Convert gRPC request to Job object.

        Args:
            request: The gRPC request containing job data.

        Returns:
            Job: The Job object.

        Raises:
            ValueError: If the request data is invalid.

        """
        job_json = getattr(request, "job_json", "")

        # Validate the request
        if not job_json:
            msg = "job json in the request data is empty"
            raise ValueError(msg)
        logger.debug("received job_json", extra={"job_json": job_json})

        try:
            job_dict = json.loads(job_json)
            job = Job(**job_dict)
            logger.debug(
                "converted strings of job json to a Job object",
                extra={"job": job}
            )
        except json.JSONDecodeError as e:
            msg = "failed to decode string of job_json to JSON dict"
            raise ValueError(msg) from e
        except Exception as e:
            msg = "failed to convert JSON to a Job object"
            raise ValueError(msg) from e

        return job
