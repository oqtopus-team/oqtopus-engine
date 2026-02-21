from __future__ import annotations

import asyncio
import logging
import time
from enum import StrEnum
from typing import TYPE_CHECKING

from .buffer import Buffer
from .step import Step

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .context import GlobalContext, JobContext
    from .exception_handler import PipelineExceptionHandler
    from .model import Job

logger = logging.getLogger(__name__)


class StepPhase(StrEnum):
    """Execution phase of a pipeline step."""

    PRE_PROCESS = "pre_process"
    POST_PROCESS = "post_process"


class PipelineExecutor:
    """Executor for a linear job-processing pipeline.

    This executor runs Jobs through a sequence of Steps and Buffers.

    Responsibilities:

    - Execute Jobs from index 0 in the PRE_PROCESS phase.
    - Interrupt forward execution at Buffers and hand off Jobs to worker tasks.
    - Resume processing after Buffers in worker threads/tasks.
    - Perform a full POST_PROCESS backward pass after reaching the end.

    The executor controls *execution*, while the list of Steps and Buffers
    represents the pipeline *definition*.

    """

    def __init__(
        self,
        pipeline: list[Step | Buffer],
        job_buffer: Buffer,
        exception_handler: PipelineExceptionHandler | None = None,
    ) -> None:
        """Initialize the pipeline executor."""
        self._pipeline = pipeline
        self._job_buffer = job_buffer
        self._exception_handler = exception_handler
        self._workers: list[asyncio.Task] = []

        # Track tasks created by execute_pipeline
        self._executing_sequence_set: set[asyncio.Task] = set()

        # Control flag for the main loop
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Start pipeline workers and enter a long-running idle loop."""
        for index, node in enumerate(self._pipeline):
            if isinstance(node, Buffer):
                task = asyncio.create_task(self._worker_loop(node, index))
                self._workers.append(task)

        # Block forever until stop() is called
        await self._stop_event.wait()

    async def stop(self) -> None:
        """Wake up start() and let the process exit (no graceful shutdown)."""
        self._stop_event.set()

    async def _worker_loop(self, buffer: Buffer, buffer_index: int) -> None:
        while True:
            try:
                gctx, jctx, job = await buffer.get()
                await self._run_from(
                    step_phase=StepPhase.PRE_PROCESS,
                    index=buffer_index + 1,
                    gctx=gctx,
                    jctx=jctx,
                    job=job,
                )
            except Exception:
                logger.exception(
                    "worker crashed and recovered",
                    extra={"buffer_index": buffer_index},
                )
                continue

    async def execute_pipeline(
        self, gctx: GlobalContext, jctx: JobContext, job: Job
    ) -> None:
        """Run the job through the ready step pipeline and enqueue it for processing."""
        # Submit the job to the pipeline and schedule it for concurrent execution
        executing_sequence = asyncio.create_task(
            self._run_from(
                step_phase=StepPhase.PRE_PROCESS,
                index=0,
                gctx=gctx,
                jctx=jctx,
                job=job,
            )
        )

        # Keep a reference to prevent the task from being garbage-collected,
        # and remove the reference when the task is completed
        self._executing_sequence_set.add(executing_sequence)
        executing_sequence.add_done_callback(self._executing_sequence_set.discard)

    async def _run_from(
        self,
        step_phase: StepPhase,
        index: int,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Run a job from the given pipeline index and phase.

        This function executes both forward (PRE_PROCESS) and backward (POST_PROCESS)
        phases, handling Buffers, safe step execution, and transition to the backward
        phase at pipeline end.

        Args:
            step_phase: The current phase of execution (pre_process or post_process).
            index: The current index in the pipeline to execute from.
            gctx: The global context.
            jctx: The job context.
            job: The job being processed.

        """
        # log start of the entire pipeline execution
        if index == 0 and step_phase == StepPhase.PRE_PROCESS:
            logger.info(
                "job processing started",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "jctx": jctx,
                },
            )

        # ============================================================
        # post-process (backward execution)
        # ============================================================
        if step_phase == StepPhase.POST_PROCESS:
            pos = index
            while pos >= 0:
                node = self._pipeline[pos]

                # Only Steps have post_process
                if isinstance(node, Step):
                    await self._safe_call(
                        fn=node.post_process,
                        gctx=gctx,
                        jctx=jctx,
                        job=job,
                        step=node,
                        phase=StepPhase.POST_PROCESS,
                    )

                pos -= 1

            # log completion of the entire pipeline execution
            logger.info(
                "job processing finished",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "jctx": jctx,
                },
            )
            return

        # ============================================================
        # pre-process (forward execution)
        # ============================================================
        while index < len(self._pipeline):
            node = self._pipeline[index]

            # -------------- Buffer encountered --------------
            if isinstance(node, Buffer):
                # Enqueue into buffer and stop forward execution
                await node.put(gctx, jctx, job)
                return

            # -------------- Step encountered --------------
            await self._safe_call(
                fn=node.pre_process,
                gctx=gctx,
                jctx=jctx,
                job=job,
                step=node,
                phase=StepPhase.PRE_PROCESS,
            )

            index += 1

        # ============================================================
        # End of forward pipeline → launch full backward pass
        # ============================================================
        await self._run_from(
            step_phase=StepPhase.POST_PROCESS,
            index=len(self._pipeline) - 1,
            gctx=gctx,
            jctx=jctx,
            job=job,
        )

    async def _safe_call(  # noqa: PLR0913, PLR0917
        self,
        fn: Callable[[GlobalContext, JobContext, Job], Awaitable[None]],
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
        step: Step,
        phase: StepPhase,
    ) -> bool:
        """Call a step function with exception handling and optional error handler.

        Args:
            fn: The function to call.
            gctx: The global context.
            jctx: The job context.
            job: The job object.
            step: The step instance.
            phase: The phase of the pipeline (pre_process or post_process).

        Returns:
            True if the function executed successfully, False otherwise.

        """
        try:
            # Starting log
            logger.info(
                "starting step phase",
                extra={
                    "step": step.__class__.__name__,
                    "phase": phase.value,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )

            # Call the function and measure elapsed time
            start = time.perf_counter()
            await fn(gctx, jctx, job)
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            # Completed log
            logger.info(
                "completed step phase",
                extra={
                    "elapsed_ms": round(elapsed_ms, 3),
                    "step": step.__class__.__name__,
                    "phase": phase.value,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
        except Exception as e:
            logger.exception(
                "failed to execute step phase",
                extra={
                    "step": step.__class__.__name__,
                    "phase": phase.value,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
            if self._exception_handler:
                await self._exception_handler.handle_exception(e, gctx, jctx, job)
            return False
        else:
            return True
