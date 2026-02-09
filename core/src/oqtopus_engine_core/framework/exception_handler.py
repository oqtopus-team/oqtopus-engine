from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import GlobalContext, JobContext
    from .model import Job


class PipelineExceptionHandler(ABC):
    """Abstract base class for handling exceptions in the pipeline."""

    @abstractmethod
    async def handle_exception(
        self,
        ex: Exception,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Handle an exception."""
        message = (
            "`handle_exception` must be implemented in subclasses of "
            "PipelineExceptionHandler"
        )
        raise NotImplementedError(message)
