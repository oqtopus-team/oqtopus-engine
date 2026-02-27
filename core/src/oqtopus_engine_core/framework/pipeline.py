from __future__ import annotations

import asyncio
import logging
import time
from enum import StrEnum
from typing import TYPE_CHECKING

from .buffer import Buffer
from .step import (
    DetachOnPostprocess,
    DetachOnPreprocess,
    JoinOnPostprocess,
    JoinOnPreprocess,
    SplitOnPostprocess,
    SplitOnPreprocess,
    Step,
)

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

        # Tracks how many children remain before a parent can be joined.
        self._pending_children: dict[str, int] = {}
        # Protects access to _pending_children to make join handling race-safe.
        self._pending_children_lock = asyncio.Lock()

        # Tracks all background tasks (root and child pipelines, parent resumes).
        self._background_tasks: set[asyncio.Task] = set()

        # Control flag for the main loop
        self._stop_event = asyncio.Event()

        logger.info(
            "pipeline executor initialized",
            extra={
                "pipeline": self._pipeline,
                "job_buffer": self._job_buffer,
                "exception_handler": self._exception_handler,
            },
        )

    async def start(self) -> None:
        """Start pipeline workers and enter a long-running idle loop."""
        for index, node in enumerate(self._pipeline):
            if isinstance(node, Buffer):
                # Spawn one worker per allowed concurrency level.
                for _ in range(node.max_concurrency):
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
        task = asyncio.create_task(
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
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_from(  # noqa: C901, PLR0911, PLR0912
        self,
        step_phase: StepPhase,
        index: int,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Run a job through the pipeline as a simple state machine.

        The state is represented by (step_phase, cursor). The method:
          - Moves forward in PRE_PROCESS phase.
          - Moves backward in POST_PROCESS phase.
          - Delegates split/join behavior to helper methods.
          - Delegates any advanced buffering or joining logic inside Buffer
            implementations (the framework only enqueues and stops).

        Args:
            step_phase: The current phase of execution (pre_process or post_process).
            index: The current index in the pipeline to execute from.
            gctx: The global context.
            jctx: The job context.
            job: The job being processed.

        """
        # log the start of the whole pipeline execution.
        if index == 0 and step_phase == StepPhase.PRE_PROCESS:
            logger.info(
                "job processing started",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )

        # state variables (do not mutate function arguments).
        current_phase = step_phase
        cursor = index

        while True:
            # ========================================================
            # phase-dependent terminal checks and phase transitions
            # ========================================================
            if current_phase == StepPhase.PRE_PROCESS:
                # end of forward pipeline: switch to full backward pass.
                if cursor >= len(self._pipeline):
                    current_phase = StepPhase.POST_PROCESS
                    cursor = len(self._pipeline) - 1
                    continue
            # backward finished: no more nodes to process.
            elif cursor < 0:
                # When a child job finishes its entire pipeline without
                # triggering a join, we must decrement the pending-children
                # counters to avoid resource leaks. This will also cascade
                # to ancestor parents if their counters reach zero.
                if job.parent is not None:
                    await self._cascade_cleanup(job)

                logger.info(
                    "job processing finished",
                    extra={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "jctx": jctx,
                    },
                )
                return

            # at this point cursor must be valid (0 <= cursor < len)
            node = self._pipeline[cursor]
            # record execution trace for debugging ---
            jctx.step_history.append((current_phase.value, cursor))

            # ========================================================
            # handle Buffer nodes
            # ========================================================
            if isinstance(node, Buffer):
                if current_phase == StepPhase.PRE_PROCESS:
                    # Delegate all buffering, scheduling, and any join-like
                    # semantics to the Buffer implementation itself.
                    await node.put(gctx, jctx, job)
                    return
                # In POST_PROCESS, Buffers are not expected. If they appear,
                # the safest option is to skip them and continue backward.
                cursor -= 1
                continue

            # from here, we expect Step-like nodes.
            # ========================================================
            # PRE_PROCESS: forward execution
            # ========================================================
            if current_phase == StepPhase.PRE_PROCESS:
                success = await self._safe_call(
                    fn=node.pre_process,
                    gctx=gctx,
                    jctx=jctx,
                    job=job,
                    step=node,
                    phase=StepPhase.PRE_PROCESS,
                )
                if not success:
                    # stop the pipeline for this job if the step failed.
                    return

                next_cursor = cursor + 1

                # ----- detach on PRE_PROCESS -----
                if isinstance(node, DetachOnPreprocess):
                    # Continue pipeline asynchronously
                    if next_cursor < len(self._pipeline):
                        logger.info(
                            "detach executed",
                            extra={
                                "job_id": job.job_id,
                                "job_type": job.job_type,
                                "phase": StepPhase.PRE_PROCESS,
                                "next_cursor": next_cursor,
                            }
                        )

                        task = asyncio.create_task(
                            self._run_from(
                                step_phase=StepPhase.PRE_PROCESS,
                                index=next_cursor,
                                gctx=gctx,
                                jctx=jctx,
                                job=job,
                            )
                        )
                        self._background_tasks.add(task)
                    return  # Worker returns immediately

                # ----- join on PRE_PROCESS (children only) -----
                if isinstance(node, JoinOnPreprocess):
                    await self._handle_join(
                        step=node,
                        step_phase=StepPhase.PRE_PROCESS,
                        next_index=next_cursor,
                        gctx=gctx,
                        jctx=jctx,
                        job=job,
                    )
                    return  # stop forward execution for this child job.

                # ----- split on PRE_PROCESS -----
                if isinstance(node, SplitOnPreprocess):
                    await self._handle_split(
                        step=node,
                        step_phase=StepPhase.PRE_PROCESS,
                        next_index=next_cursor,
                        gctx=gctx,
                        jctx=jctx,
                        job=job,
                    )
                    return  # parent stops; children will be scheduled separately.

                # normal step: just move forward.
                cursor = next_cursor
                continue

            # ========================================================
            # POST_PROCESS: backward execution
            # ========================================================
            # only Step instances are expected to implement post_process.
            if isinstance(node, Step):
                success = await self._safe_call(
                    fn=node.post_process,
                    gctx=gctx,
                    jctx=jctx,
                    job=job,
                    step=node,
                    phase=StepPhase.POST_PROCESS,
                )
                if not success:
                    # stop the pipeline for this job if the step failed.
                    return

                next_cursor = cursor - 1

                # ----- detach on POST_PROCESS -----
                if isinstance(node, DetachOnPostprocess):
                    # Continue pipeline asynchronously
                    if next_cursor < len(self._pipeline):
                        logger.info(
                            "detach executed",
                            extra={
                                "job_id": job.job_id,
                                "job_type": job.job_type,
                                "phase": StepPhase.POST_PROCESS,
                                "next_cursor": next_cursor,
                            }
                        )

                        task = asyncio.create_task(
                            self._run_from(
                                step_phase=StepPhase.POST_PROCESS,
                                index=next_cursor,
                                gctx=gctx,
                                jctx=jctx,
                                job=job,
                            )
                        )
                        self._background_tasks.add(task)
                    return  # Worker returns immediately

                # ----- join on POST_PROCESS (children only) -----
                if isinstance(node, JoinOnPostprocess):
                    # parent should resume from the next step after the join.
                    await self._handle_join(
                        step=node,
                        step_phase=StepPhase.POST_PROCESS,
                        next_index=next_cursor,
                        gctx=gctx,
                        jctx=jctx,
                        job=job,
                    )
                    return  # stop backward execution for this child job.

                # ----- split on POST_PROCESS -----
                if isinstance(node, SplitOnPostprocess):
                    # children created from a post-process split start from
                    # the step immediately after the splitter.
                    await self._handle_split(
                        step=node,
                        step_phase=StepPhase.POST_PROCESS,
                        next_index=next_cursor,
                        gctx=gctx,
                        jctx=jctx,
                        job=job,
                    )
                    return  # stop backward execution for the current job.

            # for non-step nodes in POST_PROCESS (unexpected) or normal steps,
            # simply move to the previous index.
            cursor -= 1

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

            # If this job is a child of a split parent, cancel the pending-children
            # counter so that the parent is not resumed by a later join.
            if job.parent is not None:
                await self._cancel_pending_children_for_parent(job)

            if self._exception_handler:
                await self._exception_handler.handle_exception(e, gctx, jctx, job)

            return False
        else:
            return True

    async def _handle_split(  # noqa: PLR0913, PLR0917
        self,
        step: Step,
        step_phase: StepPhase,
        next_index: int,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Handle a split event triggered by a Step.

        A split occurs when a Step implementing SplitOnPreprocess or
        SplitOnPostprocess finishes execution and has populated both
        jctx.children (child contexts) and job.children (child jobs).

        Responsibilities:
        - Validate children consistency.
        - Initialize pending-children counter.
        - Start child pipelines.
        - Stop the parent pipeline.

        Raises:
            RuntimeError: If the children are not properly set up for the split.

        """
        # ------------------------------------------------------------
        # 1. Validate children existence and consistency
        # ------------------------------------------------------------
        if not job.children:
            message = "split requested but job.children is empty"
            logger.error(
                message,
                extra={
                    "step": step.__class__.__name__,
                    "phase": step_phase.value,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
            raise RuntimeError(message)

        if not jctx.children:
            message = "split requested but jctx.children is empty"
            logger.error(
                message,
                extra={
                    "step": step.__class__.__name__,
                    "phase": step_phase.value,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
            raise RuntimeError(message)

        if len(jctx.children) != len(job.children):
            message = (
                "split requested but number of jctx.children "
                "does not match number of job.children"
            )
            logger.error(
                message,
                extra={
                    "step": step.__class__.__name__,
                    "phase": step_phase.value,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "job_children_count": len(job.children),
                    "jctx_children_count": len(jctx.children),
                },
            )
            raise RuntimeError(message)

        # ------------------------------------------------------------
        # 2. Initialize pending-child counter for join handling
        # ------------------------------------------------------------
        parent_id = job.job_id
        child_count = len(job.children)
        async with self._pending_children_lock:
            # Overwrite is allowed but indicates a nested split on the same parent.
            # For now we just log it to make debugging easier.
            if parent_id in self._pending_children:
                logger.warning(
                    "overwriting pending-children counter for parent (nested split?)",
                    extra={
                        "parent_job_id": parent_id,
                        "old_value": self._pending_children[parent_id],
                        "new_value": child_count,
                    },
                )
            self._pending_children[parent_id] = child_count

        # ------------------------------------------------------------
        # 3. Start child pipelines (and wait for them)
        # ------------------------------------------------------------
        child_coroutines: list[Awaitable[None]] = []

        for child_job, child_jctx in zip(job.children, jctx.children, strict=True):
            logger.info(
                "start child pipeline",
                extra={
                    "parent_job_id": parent_id,
                    "child_job_id": child_job.job_id,
                    "child_job_type": child_job.job_type,
                },
            )

            # Establish parent link
            child_job.parent = job
            child_jctx.parent = jctx

            # Enqueue child pipelines as coroutines; they will run concurrently
            # via asyncio.gather below.
            child_coroutines.append(
                self._run_from(
                    step_phase=step_phase,
                    index=next_index,
                    gctx=gctx,
                    jctx=child_jctx,
                    job=child_job,
                )
            )

        logger.info(
            "parent pipeline stopped after split (waiting for children)",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "phase": step_phase.value,
            },
        )

        # Run all child pipelines concurrently and wait for completion.
        if child_coroutines:
            await asyncio.gather(*child_coroutines)

        logger.info(
            "all child pipelines completed after split",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
            },
        )

        # ------------------------------------------------------------
        # 4. Parent pipeline stops here; children continue independently
        # ------------------------------------------------------------

    def _start_child_pipeline(
        self,
        *,
        step_phase: StepPhase,
        start_index: int,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Start a child pipeline as a background task.

        This helper is used by split/join logic to run child jobs in parallel.
        The child continues from the given pipeline index and phase.
        """
        task = asyncio.create_task(
            self._run_from(
                step_phase=step_phase,
                index=start_index,
                gctx=gctx,
                jctx=jctx,
                job=job,
            )
        )

        # Keep a strong reference so the task is not garbage-collected and
        # so that we can track its lifetime (for debugging and shutdown).
        self._background_tasks.add(task)

        def _done(t: asyncio.Task[None]) -> None:
            self._background_tasks.discard(t)
            try:
                # Propagate any exception to logs if not already handled.
                t.result()
            except asyncio.CancelledError:
                logger.info(
                    "child pipeline task cancelled",
                    extra={"job_id": job.job_id},
                )
            except Exception:
                logger.exception(
                    "child pipeline task failed",
                    extra={"job_id": job.job_id},
                )

        task.add_done_callback(_done)

    async def _handle_join(  # noqa: C901, PLR0912, PLR0913, PLR0917
        self,
        step: Step,
        step_phase: StepPhase,
        next_index: int,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Handle a join event triggered by a Step.

        A join occurs when a Step implementing JoinOnPreprocess or
        JoinOnPostprocess finishes execution. Each child job reaches the join
        step independently; only the last completed child triggers merging and
        resumes the parent pipeline.

        Responsibilities:
        - Decrement the pending-children counter for the parent.
        - Detect whether this child is the last to finish.
        - If not last → stop this child pipeline.
        - If last → call join_jobs() on the step, then resume the parent.

        Raises:
            RuntimeError: If the job does not have a parent or if the parent is not
            being tracked for pending children.

        """
        # ------------------------------------------------------------
        # 1. Identify the parent job
        # ------------------------------------------------------------
        parent_job = job.parent
        if parent_job is None:
            message = "join requested but job.parent is None"
            logger.error(
                message,
                extra={
                    "step": step.__class__.__name__,
                    "phase": step_phase.value,
                    "job_id": job.job_id,
                },
            )
            raise RuntimeError(message)

        parent_id = parent_job.job_id

        if parent_id not in self._pending_children:
            message = "join requested but parent has no pending-children counter"
            logger.error(
                message,
                extra={
                    "step": step.__class__.__name__,
                    "phase": step_phase.value,
                    "job_id": job.job_id,
                    "parent_job_id": parent_id,
                },
            )
            raise RuntimeError(message)

        # ------------------------------------------------------------
        # 2. Decrement pending counter (race-safe)
        # ------------------------------------------------------------
        async with self._pending_children_lock:
            if parent_id not in self._pending_children:
                message = "join requested but parent has no pending-children counter"
                logger.error(
                    message,
                    extra={
                        "step": step.__class__.__name__,
                        "phase": step_phase.value,
                        "job_id": job.job_id,
                        "parent_job_id": parent_id,
                    },
                )
                raise RuntimeError(message)

            self._pending_children[parent_id] -= 1
            remaining = self._pending_children[parent_id]
            is_last_child = remaining == 0
            if is_last_child:
                # Remove counter; join is now complete
                del self._pending_children[parent_id]

        logger.info(
            "child reached join point",
            extra={
                "step": step.__class__.__name__,
                "phase": step_phase.value,
                "child_job_id": job.job_id,
                "parent_job_id": parent_id,
                "remaining_children": remaining,
            },
        )

        # ------------------------------------------------------------
        # 3. If not last child → stop child pipeline
        # ------------------------------------------------------------
        if not is_last_child:
            logger.info(
                "child pipeline ends (not last child)",
                extra={
                    "child_job_id": job.job_id,
                    "parent_job_id": parent_id,
                },
            )
            return

        # ------------------------------------------------------------
        # 4. Last child: perform join and resume parent
        # ------------------------------------------------------------
        logger.info(
            "last child reached join point → merging begins",
            extra={
                "child_job_id": job.job_id,
                "parent_job_id": parent_id,
            },
        )

        # Parent's jctx is the one that triggered the original split
        parent_jctx = jctx.parent
        if parent_jctx is None:
            message = "parent jctx is None during join"
            logger.error(
                message,
                extra={
                    "step": step.__class__.__name__,
                    "parent_job_id": parent_id,
                },
            )
            raise RuntimeError(message)

        # ------------------------------------------------------------
        # 4-A. Execute the step-provided join function
        # ------------------------------------------------------------
        try:
            logger.info(
                "executing join_jobs on step",
                extra={
                    "step": step.__class__.__name__,
                    "parent_job_id": parent_id,
                },
            )
            await step.join_jobs(
                gctx=gctx,
                parent_jctx=parent_jctx,
                parent_job=parent_job,
                last_child=job,
            )
        except Exception:
            logger.exception(
                "join_jobs failed",
                extra={
                    "step": step.__class__.__name__,
                    "parent_job_id": parent_id,
                },
            )
            # At this point the pending counter is already removed; the parent
            # will not be resumed automatically. The exception handler (if any)
            # is responsible for propagating the failure state.
            raise

        logger.info(
            "join_jobs completed → resuming parent pipeline",
            extra={
                "parent_job_id": parent_id,
            },
        )

        # ------------------------------------------------------------
        # 4-B. Resume parent pipeline synchronously)
        # ------------------------------------------------------------
        if step_phase == StepPhase.POST_PROCESS:
            # Resume parent's POST_PROCESS phase from the requested index.
            if 0 <= next_index < len(self._pipeline):
                await self._run_from(
                    step_phase=StepPhase.POST_PROCESS,
                    index=next_index,
                    gctx=gctx,
                    jctx=parent_jctx,
                    job=parent_job,
                )
            else:
                # No steps after the join for the parent; nothing to do.
                logger.info(
                    "no parent post-process steps after join; skipping resume",
                    extra={
                        "parent_job_id": parent_id,
                        "next_index": next_index,
                    },
                )
        elif step_phase == StepPhase.PRE_PROCESS:
            # JoinOnPreprocess could resume pre-process here.
            if 0 <= next_index < len(self._pipeline):
                await self._run_from(
                    step_phase=StepPhase.PRE_PROCESS,
                    index=next_index,
                    gctx=gctx,
                    jctx=parent_jctx,
                    job=parent_job,
                )
            else:
                # Join is at the end of the pipeline.
                # Parent has no more PRE_PROCESS steps, so we directly start
                #  the POST_PROCESS pass from the last pipeline element.
                await self._run_from(
                    step_phase=StepPhase.POST_PROCESS,
                    index=len(self._pipeline) - 1,
                    gctx=gctx,
                    jctx=parent_jctx,
                    job=parent_job,
                )

    async def _cancel_pending_children_for_parent(self, child_job: Job) -> None:
        """Best-effort cleanup for pending-children state when a child fails.

        If a child job raises an exception before reaching the join step,
        we cancel the pending-children counter for its parent. This prevents
        the parent from being resumed by a later join and avoids leaving a
        stale counter in the executor.
        """
        parent = child_job.parent
        if parent is None:  # root job or no-parent job: nothing to clean
            return

        parent_job_id = parent.job_id

        async with self._pending_children_lock:
            if parent_job_id in self._pending_children:
                logger.info(
                    "cancelling pending children due to child failure",
                    extra={
                        "parent_job_id": parent_job_id,
                        "child_job_id": child_job.job_id,
                    },
                )
                del self._pending_children[parent_job_id]

    async def _cascade_cleanup(self, job: Job) -> None:
        """Cascade cleanup for non-joined child jobs.

        This method is called when a job with a parent reaches the end of its
        pipeline *without* triggering a join. It decrements the parent's
        pending-children counter, and if it reaches zero, recursively applies
        the same logic to the ancestor chain.

        This guarantees that:
        - split-only pipelines do not leak pending-children entries, and
        - multi-level splits are cleaned up correctly without invoking join.
        """
        current = job

        while True:
            parent = current.parent
            if parent is None:
                # Reached the root; nothing more to cascade.
                return

            parent_id = parent.job_id

            async with self._pending_children_lock:
                if parent_id not in self._pending_children:
                    # Parent is not tracked for pending children; nothing to do
                    # for this branch of the ancestor chain.
                    return

                self._pending_children[parent_id] -= 1
                remaining = self._pending_children[parent_id]

                logger.info(
                    "decremented pending children due to child pipeline completion",
                    extra={
                        "parent_job_id": parent_id,
                        "child_job_id": current.job_id,
                        "remaining_children": remaining,
                    },
                )

                if remaining > 0:
                    # Parent still has other children running; stop cascading.
                    return

                # remaining == 0: all children of this parent are done.
                # We can safely remove the counter entry and cascade upward.
                del self._pending_children[parent_id]

            # Move one level up the ancestor chain and continue.
            current = parent
