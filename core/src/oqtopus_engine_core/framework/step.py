from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import GlobalContext, JobContext
    from .model import Job


class Step(ABC):
    """Abstract base class for pipeline steps with pre and post process hooks."""

    @abstractmethod
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Run before the job is enqueued for processing."""
        message = "`pre_process` must be implemented in subclasses of Step"
        raise NotImplementedError(message)

    @abstractmethod
    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Run after the job has been processed."""
        message = "`post_process` must be implemented in subclasses of Step"
        raise NotImplementedError(message)
