from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .context import GlobalContext, JobContext
    from .model import Job


class Buffer(Protocol):
    """Abstract interface for pipeline buffers.

    A Buffer stores `(gctx, jctx, job)` tuples and acts as an intermediate
    component between pipeline steps. Different Buffer implementations may
    apply different queuing or batching strategies.

    Each stored element consists of:
        - gctx: GlobalContext (engine-wide shared context)
        - jctx: JobContext (per-job mutable context)
        - job:  Job (raw job description from OQTOPUS Cloud)

    Implementations must provide asynchronous `put()` and `get()` methods,
    along with a synchronous `size()` method for monitoring capacity.
    """

    async def put(self, gctx: GlobalContext, jctx: JobContext, job: Job) -> None:
        """Store a job tuple into the buffer.

        Args:
            gctx: Global execution context associated with the job.
            jctx: Job-specific context.
            job: The raw job data.

        """
        ...

    async def get(self) -> tuple[GlobalContext, JobContext, Job]:
        """Retrieve a stored job tuple from the buffer.

        Returns:
            A tuple `(gctx, jctx, job)` removed from the buffer.

        """
        ...

    def size(self) -> int:
        """Return the number of items stored in the buffer.

        Returns:
            The number of queued elements.

        """
        ...
