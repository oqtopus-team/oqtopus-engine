from __future__ import annotations

import asyncio

from oqtopus_engine_core.framework import Buffer, GlobalContext, Job, JobContext


class QueueBuffer(Buffer):
    """FIFO buffer implementation backed by `asyncio.Queue`.

    This class provides a simple first-in/first-out buffer for transporting
    `(gctx, jctx, job)` tuples between pipeline components. All operations are
    asynchronous and thread-safe under Python's asyncio model.

    This implementation is suitable for most common use cases. Specialized
    buffers—such as those performing automatic combining, batching, or
    dependency-aware gating—can be built by extending or composing this class.

    Args:
        maxsize: Maximum number of elements allowed in the queue.
            A value of 0 indicates unlimited capacity.

    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    async def put(self, gctx: GlobalContext, jctx: JobContext, job: Job) -> None:
        """Insert a tuple into the buffer.

        Args:
            gctx: Global execution context.
            jctx: Job-specific context.
            job: The raw job data.

        Note:
            This operation waits if the queue is full (only when `maxsize > 0`).

        """
        await self._queue.put((gctx, jctx, job))

    async def get(self) -> tuple[GlobalContext, JobContext, Job]:
        """Remove and return a tuple from the buffer.

        Returns:
            A tuple `(gctx, jctx, job)` retrieved from the queue.

        Note:
            This operation waits until an item becomes available.

        """
        return await self._queue.get()

    def size(self) -> int:
        """Return the current number of queued items.

        Returns:
            The size of the underlying queue.

        """
        return self._queue.qsize()
