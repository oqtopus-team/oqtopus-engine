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
)
from oqtopus_engine_core.framework.context import HAS_ORIGINAL_JOB_CHILDREN_KEY
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2, qpu_pb2_grpc

logger = logging.getLogger(__name__)


def _collect_status_update_targets(
    jctx: JobContext,
    job: Job,
) -> list[Job]:
    """Collect jobs to be updated.

    When the context has HAS_ORIGINAL_JOB_CHILDREN_KEY, the leaf children are the actual
    execution units sent to the device — return them directly.
    Otherwise traverse down to leaves and then up to root parents.

    Args:
        jctx: The job context of the current job.
        job: The current job.

    Returns:
        A list of Job objects to be updated.

    """
    leaf_pairs = _find_all_leaf_jobs(jctx, job)

    if HAS_ORIGINAL_JOB_CHILDREN_KEY in jctx:
        # The leaves are the repository-tracked children being executed on devices.
        return [leaf_job for _, leaf_job in leaf_pairs]

    # Traverse upwards from each leaf to find the user-visible root jobs.
    unique_jobs: dict[str, Job] = {}
    visited_up: set[str] = set()
    for leaf_jctx, leaf_job in leaf_pairs:
        root_pairs = _find_all_root_jobs(leaf_jctx, leaf_job, visited=visited_up)
        for _, root_job in root_pairs:
            unique_jobs[root_job.job_id] = root_job
    return list(unique_jobs.values())


def _find_all_leaf_jobs(
    jctx: JobContext,
    job: Job,
    visited: set[str] | None = None,
) -> list[tuple[JobContext, Job]]:
    """Recursively find all terminal leaf jobs.

    Args:
        jctx: The job context of the current job.
        job: The current job.
        visited: A set of job IDs that have already been visited to prevent cycles.

    Returns:
        A list of (JobContext, Job) tuples for all leaf jobs

    """
    if visited is None:
        visited = set()

    if job.job_id in visited:
        return []
    visited.add(job.job_id)

    leaves: list[tuple[JobContext, Job]] = []

    if HAS_ORIGINAL_JOB_CHILDREN_KEY in jctx:
        # Continue traversing down if children exist
        for child_jctx, child_job in zip(jctx.children, job.children, strict=True):
            leaves.extend(_find_all_leaf_jobs(child_jctx, child_job, visited))
    else:
        # Reached a leaf node, append the pair to the list
        leaves.append((jctx, job))

    return leaves


def _find_all_root_jobs(
    jctx: JobContext,
    job: Job,
    visited: set[str] | None = None,
) -> list[tuple[JobContext, Job]]:
    """Recursively find all root jobs.

    Args:
        jctx: The job context of the current job.
        job: The current job.
        visited: A set of job IDs that have already been visited to prevent cycles.

    Returns:
        A list of (JobContext, Job) tuples for all root jobs

    """
    if visited is None:
        visited = set()

    if job.job_id in visited:
        return []
    visited.add(job.job_id)

    roots: list[tuple[JobContext, Job]] = []

    if job.parent is not None and jctx.parent is not None:
        # Continue traversing up to find the entry point of the job graph
        roots.extend(_find_all_root_jobs(jctx.parent, job.parent, visited))
    else:
        # Reached a root node, append the pair to the list
        roots.append((jctx, job))

    return roots


def _select_program(job: Job) -> str:
    transpile_result = job.transpile_result
    if transpile_result is None or transpile_result.transpiled_program is None:
        return job.program[0]  # type: ignore[index]
    return transpile_result.transpiled_program


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
        # Engine owns device access orchestration, so all jobs, including
        # internal estimation children, must serialize gateway execution here.
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
        jctx: JobContext,
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
            RuntimeError: If the device status is not available.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        start = time.perf_counter()

        async with self._execution_lock:
            # Identify all jobs that require a status update (roots and leaves)
            update_targets = _collect_status_update_targets(jctx, job)
            await self._update_jobs_status(gctx, update_targets)

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
