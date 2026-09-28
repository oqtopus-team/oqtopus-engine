import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

import grpc  # type: ignore[import-untyped]

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobResult,
    PipelineDirective,
    SamplingResult,
    Step,
    StepResult,
    resolve_repository_jobs,
)
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2, qpu_pb2_grpc

logger = logging.getLogger(__name__)


def _collect_status_update_targets(job: Job) -> list[Job]:
    """Collect the repository-tracked Job objects to update to "running".

    Args:
        job: The current job.

    Returns:
        The repository-tracked Job objects to update (already deduped by
        job_id; see `resolve_repository_jobs`).

    """
    return resolve_repository_jobs(job)


def _select_program(job: Job) -> str:
    transpile_result = job.transpile_result
    if transpile_result is None or transpile_result.transpiled_program is None:
        return job.program[0]  # type: ignore[index]
    return transpile_result.transpiled_program


def _mark_ready_descendants_running(job: Job) -> None:
    """Mark `job` and its "ready" descendants "running", locally only.

    `_update_jobs_status` only PATCHes `resolve_repository_jobs(job)`
    targets, which never include a job with no job repository record of its
    own (e.g. an MP-combined job) nor, transitively, that job's own
    descendants (e.g. estimation children combined into it — they never
    reach this step individually). Without this, their local status stays
    "ready" for the rest of the run. A job_id visited-set guards against
    cycles, though parent/child links are one-directional by convention.
    """
    visited: set[str] = set()
    stack = [job]
    while stack:
        current = stack.pop()
        if current.job_id in visited:
            continue
        visited.add(current.job_id)
        if current.status == "ready":
            current.status = "running"
        stack.extend(current.children)


class DeviceGatewayStep(Step):
    """Step that sends a job to the device gateway via gRPC during pre_process."""

    def __init__(
        self,
        gateway_address: str = "localhost:50051",
        grpc_options: Sequence[tuple[str, Any]] | None = None,
    ) -> None:
        self._channel = grpc.aio.insecure_channel(
            gateway_address,
            options=grpc_options,
        )
        self._stub = qpu_pb2_grpc.QpuServiceStub(self._channel)
        # Guards the "ready" -> "running" check-and-set below (status
        # update + local bookkeeping) so concurrent sibling estimation
        # jobs never transition the same parent twice. It does NOT
        # serialize gateway execution: GetServiceStatus and CallJob run
        # outside this lock. Gateway execution is serialized separately,
        # by the buffer's max_concurrency (see config.yaml).
        self._execution_lock = asyncio.Lock()
        logger.info(
            "DeviceGatewayStep was initialized",
            extra={
                "gateway_address": gateway_address,
                "grpc_options": grpc_options,
            },
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> StepResult:
        """Pre-process the job by sending a request to the device gateway.

        This method sends a gRPC request to the device gateway for job execution,
        and updates the job with the result.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            RuntimeError: If the device status is not available, if `job`
                is an unsplit estimation job, or if `job.job_type` is not
                one this step supports.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        start = time.perf_counter()

        async with self._execution_lock:
            # Identify all jobs that require a status update
            update_targets = _collect_status_update_targets(job)
            await self._update_jobs_status(gctx, update_targets)
            # Local-only bookkeeping for jobs with no job repository record
            # of their own; kept out of the PATCH loop above so it never
            # sends a status update for a job `_is_repository_tracked`
            # would reject.
            _mark_ready_descendants_running(job)

        # Check device status immediately before using the gateway.
        service_status = await self._stub.GetServiceStatus(
            qpu_pb2.GetServiceStatusRequest()  # type: ignore[attr-defined]
        )
        logger.info(
            "GetServiceStatus response",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "service_status": service_status.service_status,
            },
        )
        if service_status.service_status != qpu_pb2.ServiceStatus.SERVICE_STATUS_ACTIVE:  # type: ignore[attr-defined]
            message = "device status is not available"
            raise RuntimeError(message)

        # Call device gateway
        if job.job_type in {"sampling", "multi_manual"}:
            job_request = qpu_pb2.CallJobRequest(  # type: ignore[attr-defined]
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
            if job_response.status != qpu_pb2.JobStatus.JOB_STATUS_SUCCESS:  # type: ignore[attr-defined]
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
            job.result = JobResult(
                sampling=SamplingResult(counts=job_response.result.counts)
            )
            job.message = job_response.result.message
        elif job.job_type == "estimation":
            message = "estimation jobs must be split before reaching device gateway"
            raise RuntimeError(message)
        else:
            message = f"unsupported job_type at device gateway: {job.job_type}"
            raise RuntimeError(message)
        return StepResult()

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,  # noqa: ARG002
        job: Job,  # noqa: ARG002
    ) -> StepResult:
        """Post-process the job; detach so subsequent steps run asynchronously.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Returns:
            StepResult: DETACH directive — spawns a background task and returns.

        """
        return StepResult(directive=PipelineDirective.DETACH)

    @staticmethod
    async def _update_jobs_status(gctx: GlobalContext, jobs: list[Job]) -> None:
        """Update the job status to "running" for the given jobs."""
        for job in jobs:
            if job.status == "ready":
                job.status = "running"
                # No outputs exist yet at this transition, so output_files is
                # irrelevant here.
                await gctx.job_repository.update_job_status_nowait(  # type: ignore[union-attr]
                    job, include_output_files=False
                )
