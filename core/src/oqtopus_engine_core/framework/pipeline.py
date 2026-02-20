from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .buffer import Buffer
    from .context import GlobalContext, JobContext
    from .exception_handler import PipelineExceptionHandler
    from .model import Job
    from .step import Step

logger = logging.getLogger(__name__)


class PipelineExecutor:
    """Coordinates the execution of a job through a pipeline of steps."""

    def __init__(
        self,
        before_buffer_steps: list[Step],
        job_buffer: Buffer,
        after_buffer_steps: list[Step],
        exception_handler: PipelineExceptionHandler | None = None,
    ) -> None:
        """Initialize the pipeline executor."""
        self._before_buffer_steps = before_buffer_steps
        self._after_buffer_steps = after_buffer_steps
        self._job_buffer = job_buffer
        self._exception_handler = exception_handler
        self._buffer_fetching_worker_task: asyncio.Task[None] | None = None
        self._executing_sequence_set: set = set()

    async def execute_pipeline(
        self, gctx: GlobalContext, jctx: JobContext, job: Job
    ) -> None:
        """Run the job through the ready step pipeline and enqueue it for processing."""
        # Submit the job to the pipeline and schedule it for concurrent execution
        executing_sequence = asyncio.create_task(
            self._execute_pipeline(gctx, jctx, job)
        )

        # Keep a reference to prevent the task from being garbage-collected,
        # and remove the reference when the task is completed
        self._executing_sequence_set.add(executing_sequence)
        executing_sequence.add_done_callback(self._executing_sequence_set.discard)

    async def _execute_pipeline(
        self, gctx: GlobalContext, jctx: JobContext, job: Job
    ) -> None:
        logger.info(
            "job processing started",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
            },
        )

        for step in self._before_buffer_steps:
            if not await self._safe_call(
                step.pre_process, gctx, jctx, job, step, "pre_process"
            ):
                logger.error(
                    "failed in ready step during pre_process",
                    extra={
                        "step": step.__class__.__name__,
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                    },
                )
                return
        logger.debug(
            "execute_pipeline",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "jctx": jctx,
            },
        )
        await self._job_buffer.put(gctx, jctx, job)

    async def start(self) -> None:
        """Start the background worker to process jobs from the buffer."""
        self._buffer_fetching_worker_task = asyncio.create_task(
            self._run_buffer_fetching_worker()
        )

    async def _run_buffer_fetching_worker(self) -> None:
        """Background task loop that runs the After Buffer Steps."""
        while True:
            gctx, jctx, job = await self._job_buffer.get()

            all_ok = True
            for step in self._after_buffer_steps:
                if not await self._safe_call(
                    step.pre_process, gctx, jctx, job, step, "pre_process"
                ):
                    all_ok = False
                    break

            if all_ok:
                for step in reversed(self._after_buffer_steps):
                    if not await self._safe_call(
                        step.post_process, gctx, jctx, job, step, "post_process"
                    ):
                        all_ok = False
                        break

            if all_ok:
                for step in reversed(self._before_buffer_steps):
                    if not await self._safe_call(
                        step.post_process, gctx, jctx, job, step, "post_process"
                    ):
                        all_ok = False
                        break

            logger.info(
                "job processing finished",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "jctx": jctx,
                },
            )

    async def _safe_call(  # noqa: PLR0913, PLR0917
        self,
        fn: Callable[[GlobalContext, JobContext, Job], Awaitable[None]],
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
        step: Step,
        phase: str,
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
                    "phase": phase,
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
                    "phase": phase,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
        except Exception as e:
            logger.exception(
                "failed to execute step phase",
                extra={
                    "step": step.__class__.__name__,
                    "phase": phase,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
            if self._exception_handler:
                await self._exception_handler.handle_exception(e, gctx, jctx, job)
            return False
        else:
            return True
