"""PipelineManager / PipelineSelector.

`PipelineManager` owns one `PipelineExecutor` per configured pipeline and
routes each incoming job to the right one, based on `PipelineSelector`.
It implements the same external interface as a single `PipelineExecutor`
(`start()` / `execute_pipeline()` / `job_buffer`), so callers such as
`Engine` and `JobFetcher` do not need to distinguish between the two.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .pipeline_condition import evaluate

if TYPE_CHECKING:
    from .buffer import Buffer
    from .context import GlobalContext, JobContext
    from .exception_handler import PipelineExceptionHandler
    from .model import Job
    from .pipeline import PipelineExecutor
    from .pipeline_condition import Expr

logger = logging.getLogger(__name__)


class NoMatchingPipelineError(Exception):
    """Raised when a job matches no pipeline's `if` condition.

    `PipelineManager.execute_pipeline()` passes this to `exception_handler`
    unconditionally whenever no pipeline matches, regardless of whether the
    config defines an explicit catch-all. Its message is surfaced to the
    end user via `job.message` (see `FailJobRepositoryHandler`).
    """

    def __init__(self, job: Job) -> None:
        super().__init__(
            f"job was not processed: no pipeline matched job_type={job.job_type!r}"
        )


class PipelineSelector:
    """Select the first pipeline whose compiled `if` condition matches a job."""

    def __init__(self, compiled_conditions: list[tuple[str, Expr]]) -> None:
        """Initialize with `(pipeline_name, compiled_condition)` pairs, in order."""
        self._compiled_conditions = compiled_conditions

    def select(self, job: Job) -> str | None:
        """Select the pipeline name for `job`.

        Returns:
            The name of the first matching pipeline, or `None` if none match.

        """
        for name, condition in self._compiled_conditions:
            if evaluate(condition, job):
                return name
        return None


class PipelineManager:
    """Own multiple `PipelineExecutor` instances and route jobs between them."""

    def __init__(
        self,
        executors: dict[str, PipelineExecutor],
        selector: PipelineSelector,
        shared_buffers: dict[Buffer, dict[str, int]],
        exception_handler: PipelineExceptionHandler,
        job_buffer: Buffer,
    ) -> None:
        """Initialize the pipeline manager.

        Args:
            executors: Pipeline name -> the `PipelineExecutor` that runs it.
            selector: Decides which pipeline a given job belongs to.
            shared_buffers: Buffer instances referenced by more than one
                pipeline's `steps`, mapped to `{pipeline_name: buffer_index}`
                for each pipeline that references them. Workers for these
                buffers are spawned centrally by this manager, not by the
                individual `PipelineExecutor` instances, so that a job
                dequeued by any worker always resumes in its own pipeline.
            exception_handler: Used to fail jobs that match no pipeline.
            job_buffer: The buffer exposed via the `job_buffer` property,
                used by `JobFetcher` for backpressure.

        """
        self._executors = executors
        self._selector = selector
        self._shared_buffers = shared_buffers
        self._exception_handler = exception_handler
        self._job_buffer = job_buffer
        self._workers: list[asyncio.Task] = []

    @property
    def job_buffer(self) -> Buffer:
        """Get the job buffer (read-only), used by JobFetcher for backpressure.

        Returns:
            The Buffer instance used for job scheduling.

        """
        return self._job_buffer

    async def start(self) -> None:
        """Start shared-buffer workers and all owned `PipelineExecutor` instances."""
        for buffer, index_by_pipeline in self._shared_buffers.items():
            for _ in range(buffer.max_concurrency):
                task = asyncio.create_task(
                    self._shared_worker_loop(buffer, index_by_pipeline)
                )
                self._workers.append(task)

        executor_starts = (executor.start() for executor in self._executors.values())
        await asyncio.gather(*executor_starts)

    async def _shared_worker_loop(
        self, buffer: Buffer, index_by_pipeline: dict[str, int]
    ) -> None:
        while True:
            try:
                gctx, jctx, job = await buffer.get()
                pipeline_name = jctx.get("pipeline_name")
                if pipeline_name is None:
                    # Some Buffer implementations build a fresh JobContext
                    # for an item they enqueue (e.g. MpAutoCombiningBuffer's
                    # combined job), which has no `pipeline_name` copied
                    # over. Re-derive it from the job itself, exactly as
                    # execute_pipeline() would for a brand-new job.
                    pipeline_name = self._selector.select(job)
                    if pipeline_name is None:
                        logger.error(
                            "no pipeline matched job dequeued from shared buffer",
                            extra={"job_id": job.job_id, "job_type": job.job_type},
                        )
                        await self._exception_handler.handle_exception(
                            NoMatchingPipelineError(job), gctx, jctx, job
                        )
                        continue
                    jctx["pipeline_name"] = pipeline_name
                executor = self._executors[pipeline_name]
                buffer_index = index_by_pipeline[pipeline_name]
                await executor.resume_from_buffer(buffer_index + 1, gctx, jctx, job)
            except Exception:
                logger.exception("shared buffer worker crashed and recovered")
                continue

    async def execute_pipeline(
        self, gctx: GlobalContext, jctx: JobContext, job: Job
    ) -> None:
        """Select a pipeline for `job` and run it.

        If no pipeline matches, fail `job` via `exception_handler` instead
        (see `NoMatchingPipelineError`).
        """
        name = self._selector.select(job)
        if name is None:
            logger.error(
                "no pipeline matched job",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            await self._exception_handler.handle_exception(
                NoMatchingPipelineError(job), gctx, jctx, job
            )
            return

        jctx["pipeline_name"] = name
        await self._executors[name].execute_pipeline(gctx, jctx, job)
