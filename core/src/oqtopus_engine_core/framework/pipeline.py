from __future__ import annotations

import asyncio
import logging
import time
from enum import StrEnum
from typing import TYPE_CHECKING

from opentelemetry import baggage, trace
from opentelemetry import context as otel_context

from .buffer import Buffer
from .context import link_parent_and_children
from .observability import (
    job_completed_counter,
    job_duration_histogram,
    job_ready_counter,
)
from .step import PipelineDirective, Step, StepResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .context import GlobalContext, JobContext
    from .exception_handler import PipelineExceptionHandler
    from .model import Job

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


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

    @property
    def job_buffer(self) -> Buffer:
        """Get the job buffer (read-only).

        Returns:
            The Buffer instance used for job scheduling.

        """
        return self._job_buffer

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
                # Worker tasks are spawned at pipeline startup with an empty
                # OTel context. Re-attach the per-job context that was saved
                # on jctx during the first `_run_from` entry so spans created
                # after a Buffer transit stay parented under the job's root
                # `oqtopus_engine.job.process` span.
                token = None
                saved_ctx = jctx.get("_oqtopus_obs_ctx")
                if saved_ctx is not None:
                    token = otel_context.attach(saved_ctx)
                try:
                    await self._run_from(
                        step_phase=StepPhase.PRE_PROCESS,
                        index=buffer_index + 1,
                        gctx=gctx,
                        jctx=jctx,
                        job=job,
                    )
                finally:
                    if token is not None:
                        otel_context.detach(token)
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

    async def _run_from(
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

        On root-job entry, it also opens the long-lived
        ``oqtopus_engine.job.process`` span and attaches the per-job OTel
        context (with ``oqtopus.*`` baggage) so child spans created during
        processing become its descendants. The context is saved on jctx so
        workers can re-attach it after Buffer transits; the span is ended
        in ``_finalize_job_observability``.

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

        # Root jobs only: open the long-lived `oqtopus_engine.job.process`
        # span and attach an OTel context carrying it (plus oqtopus.* baggage)
        # to the current task. The context is also saved on jctx so worker
        # tasks can re-attach it after a Buffer transit. The span is ended in
        # `_finalize_job_observability`.
        token = None
        if (
            index == 0
            and step_phase == StepPhase.PRE_PROCESS
            and job.parent is None
            and "_oqtopus_obs_span" not in jctx
        ):
            root_span = tracer.start_span(
                "oqtopus_engine.job.process",
                attributes={
                    "oqtopus.job_id": job.job_id,
                    "oqtopus.job_type": job.job_type,
                    "oqtopus.device_id": job.device_id,
                },
            )
            ctx = trace.set_span_in_context(root_span)
            ctx = baggage.set_baggage("oqtopus.job_id", job.job_id, context=ctx)
            ctx = baggage.set_baggage("oqtopus.job_type", job.job_type, context=ctx)
            jctx["_oqtopus_obs_span"] = root_span
            jctx["_oqtopus_obs_ctx"] = ctx
            jctx["_oqtopus_obs_start"] = time.perf_counter()
            jctx["_oqtopus_obs_finalized"] = False
            job_ready_counter.add(1, {"oqtopus.job_type": job.job_type})
            token = otel_context.attach(ctx)

        try:
            await self._run_state_machine(step_phase, index, gctx, jctx, job)
        finally:
            if token is not None:
                otel_context.detach(token)

    async def _run_state_machine(  # noqa: C901, PLR0911, PLR0912
        self,
        step_phase: StepPhase,
        index: int,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """State-machine body of ``_run_from``.

        The state is represented by (step_phase, cursor). The method:
          - Moves forward in PRE_PROCESS phase.
          - Moves backward in POST_PROCESS phase.
          - Delegates split/join behavior to helper methods.
          - Delegates any advanced buffering or joining logic inside Buffer
            implementations (the framework only enqueues and stops).

        Factored out of ``_run_from`` so the outer function can wrap it in a
        try/finally that detaches the per-invocation root-span context token
        on exit; the root span itself outlives this invocation when the
        pipeline continues via a Buffer worker.
        """
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
                self._finalize_job_observability(jctx, job, status="success")
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
                result = await self._safe_call(
                    fn=node.pre_process,
                    gctx=gctx,
                    jctx=jctx,
                    job=job,
                    step=node,
                    phase=StepPhase.PRE_PROCESS,
                )
                if result is None:
                    # stop the pipeline for this job if the step failed.
                    return

                next_cursor = cursor + 1

                # ----- detach on PRE_PROCESS -----
                if result.directive == PipelineDirective.DETACH:
                    if next_cursor < len(self._pipeline):
                        logger.info(
                            "detach executed",
                            extra={
                                "job_id": job.job_id,
                                "job_type": job.job_type,
                                "phase": StepPhase.PRE_PROCESS,
                                "next_cursor": next_cursor,
                            },
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
                if result.directive == PipelineDirective.JOIN:
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
                if result.directive in {
                    PipelineDirective.SPLIT_FOR_JOIN,
                    PipelineDirective.SPLIT_WITHOUT_JOIN,
                }:
                    await self._handle_split(
                        result=result,
                        step=node,
                        step_phase=StepPhase.PRE_PROCESS,
                        next_index=next_cursor,
                        gctx=gctx,
                        jctx=jctx,
                        job=job,
                    )
                    return  # parent stops; children will be scheduled separately.

                # NONE: normal step, just move forward.
                cursor = next_cursor
                continue

            # ========================================================
            # POST_PROCESS: backward execution
            # ========================================================
            # only Step instances are expected to implement post_process.
            if isinstance(node, Step):
                result = await self._safe_call(
                    fn=node.post_process,
                    gctx=gctx,
                    jctx=jctx,
                    job=job,
                    step=node,
                    phase=StepPhase.POST_PROCESS,
                )
                if result is None:
                    # stop the pipeline for this job if the step failed.
                    return

                next_cursor = cursor - 1

                # ----- detach on POST_PROCESS -----
                if result.directive == PipelineDirective.DETACH:
                    if next_cursor < len(self._pipeline):
                        logger.info(
                            "detach executed",
                            extra={
                                "job_id": job.job_id,
                                "job_type": job.job_type,
                                "phase": StepPhase.POST_PROCESS,
                                "next_cursor": next_cursor,
                            },
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
                if result.directive == PipelineDirective.JOIN:
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
                if result.directive in {
                    PipelineDirective.SPLIT_FOR_JOIN,
                    PipelineDirective.SPLIT_WITHOUT_JOIN,
                }:
                    # children created from a post-process split start from
                    # the step immediately before the splitter.
                    await self._handle_split(
                        result=result,
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
        fn: Callable[[GlobalContext, JobContext, Job], Awaitable[StepResult]],
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
        step: Step,
        phase: StepPhase,
    ) -> StepResult | None:
        """Call a step function with exception handling and optional error handler.

        Args:
            fn: The function to call.
            gctx: The global context.
            jctx: The job context.
            job: The job object.
            step: The step instance.
            phase: The phase of the pipeline (pre_process or post_process).

        Returns:
            The StepResult from the function, or None if an exception occurred.

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
            with tracer.start_as_current_span(
                f"oqtopus_engine.pipeline.{step.__class__.__name__}.{phase.value}",
                attributes={
                    "oqtopus.job_id": job.job_id,
                    "oqtopus.job_type": job.job_type,
                    "oqtopus.pipeline.step": step.__class__.__name__,
                    "oqtopus.pipeline.phase": phase.value,
                },
            ):
                result = await fn(gctx, jctx, job)
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

            # Record failure on the root job's observability state.
            # _safe_call swallows the exception so `asyncio.gather` in
            # `_handle_split` returns normally; without this flag the
            # split-path finalize would tag the root span as "success".
            self._mark_root_observability_failed(jctx)
            self._finalize_job_observability(jctx, job, status="failure")
            return None
        else:
            return result

    @staticmethod
    def _finalize_job_observability(jctx: JobContext, job: Job, status: str) -> None:
        """End the job-level root span and emit completion metrics.

        Safe to call multiple times for the same job; only the first call
        records metrics and ends the span. The per-invocation OTel context
        tokens are managed by their callers (`_run_from` / `_worker_loop`),
        so this function only owns the span lifecycle.

        If any descendant step has marked the root jctx via
        `_mark_root_observability_failed`, the caller's status is overridden
        to "failure" so the root span / metrics reflect the actual outcome
        rather than a child's silently-swallowed exception.
        """
        if jctx.get("_oqtopus_obs_finalized"):
            return
        if "_oqtopus_obs_span" not in jctx:
            return
        jctx["_oqtopus_obs_finalized"] = True

        if jctx.get("_oqtopus_obs_failed"):
            status = "failure"

        start = jctx.get("_oqtopus_obs_start")
        if start is not None:
            duration_s = time.perf_counter() - start
            job_duration_histogram.record(
                duration_s,
                {"oqtopus.job_type": job.job_type, "oqtopus.status": status},
            )
        job_completed_counter.add(
            1, {"oqtopus.job_type": job.job_type, "oqtopus.status": status}
        )

        root_span = jctx["_oqtopus_obs_span"]
        root_span.set_attribute("oqtopus.status", status)
        root_span.end()

    @staticmethod
    def _mark_root_observability_failed(jctx: JobContext) -> None:
        """Mark the root job's jctx as failed for observability finalization.

        Walks up `jctx.parent` until it finds the jctx that owns the
        `_oqtopus_obs_span` (i.e. the root job's jctx) and sets the
        `_oqtopus_obs_failed` flag there. This is called from step exception
        paths so that the eventual `_finalize_job_observability` on the root
        records `oqtopus.status="failure"` even when the root finalize is
        triggered from a "success" code path (e.g. after `_handle_split`'s
        `gather` returns normally because child failures are swallowed by
        `_safe_call`).
        """
        cur: JobContext | None = jctx
        while cur is not None:
            if "_oqtopus_obs_span" in cur:
                cur["_oqtopus_obs_failed"] = True
                return
            cur = cur.parent

    async def _handle_split(  # noqa: PLR0913, PLR0917
        self,
        result: StepResult,
        step: Step,
        step_phase: StepPhase,
        next_index: int,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Handle a split event triggered by a Step.

        A split occurs when a step's pre_process or post_process returns a
        StepResult with SPLIT_FOR_JOIN or SPLIT_WITHOUT_JOIN directive.

        Responsibilities:
        - Link parent job/context with the child jobs/contexts from StepResult.
        - Initialize pending-children counter.
        - Start child pipelines concurrently.
        - Stop the parent pipeline.

        Raises:
            RuntimeError: If child_jobs or child_contexts are empty.

        """
        child_jobs = result.child_jobs
        child_contexts = result.child_contexts

        # StepResult.__post_init__ guarantees these are not None for SPLIT_*.
        assert child_jobs is not None  # noqa: S101
        assert child_contexts is not None  # noqa: S101

        # ------------------------------------------------------------
        # 1. Establish parent ↔ child links
        # ------------------------------------------------------------
        link_parent_and_children(jctx, job, child_contexts, child_jobs)

        # ------------------------------------------------------------
        # 2. Validate non-empty children
        # ------------------------------------------------------------
        if not child_jobs:
            message = "split requested but child_jobs is empty"
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

        # ------------------------------------------------------------
        # 3. Initialize pending-child counter for join/cleanup handling
        # ------------------------------------------------------------
        parent_id = job.job_id
        child_count = len(child_jobs)
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

        # Propagate the parent's per-job OTel context (if any) to each child
        # via jctx. Children inherit the active context naturally when
        # spawned in this task, but the saved context is needed so workers
        # can re-attach it after a child crosses a Buffer.
        parent_obs_ctx = jctx.get("_oqtopus_obs_ctx")

        for child_job, child_jctx in zip(job.children, jctx.children, strict=True):
            logger.info(
                "start child pipeline",
                extra={
                    "parent_job_id": parent_id,
                    "child_job_id": child_job.job_id,
                    "child_job_type": child_job.job_type,
                },
            )

            if parent_obs_ctx is not None:
                child_jctx["_oqtopus_obs_ctx"] = parent_obs_ctx

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
        # 4. Finalize the parent's job-level span if this was a root job.
        # When the pipeline terminates via a split (no join resumes the
        # parent), the parent's _run_state_machine never reaches the
        # cursor<0 branch that normally calls _finalize_job_observability,
        # so it must be called here.
        # ------------------------------------------------------------
        if job.parent is None:
            self._finalize_job_observability(jctx, job, status="success")

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
                # No steps after the join for the parent; the pipeline is
                # complete. Finalize the root span here since no _run_from
                # invocation will reach the cursor<0 branch.
                logger.info(
                    "no parent post-process steps after join; skipping resume",
                    extra={
                        "parent_job_id": parent_id,
                        "next_index": next_index,
                    },
                )
                if parent_job.parent is None:
                    self._finalize_job_observability(
                        parent_jctx, parent_job, status="success"
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
