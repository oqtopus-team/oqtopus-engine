from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import GlobalContext, JobContext
    from .model import Job


class PipelineDirective(StrEnum):
    """Pipeline control directives returned by a step."""

    NONE = auto()
    """No special pipeline control. Proceed to the next step as usual."""

    SPLIT_FOR_JOIN = auto()
    """Fan out into child jobs.

    A later step is expected to JOIN them back into the parent.
    """

    SPLIT_WITHOUT_JOIN = auto()
    """Fan out into child jobs without a subsequent JOIN.

    The parent pipeline ends after the split.
    """

    JOIN = auto()
    """Fan in: merge this child job's result into the parent.

    Only the last child resumes the parent pipeline.
    """

    DETACH = auto()
    """Detach from the current pipeline flow and continue asynchronously."""


@dataclass(slots=True)
class StepResult:
    """Result returned by a step hook."""

    directive: PipelineDirective = PipelineDirective.NONE
    child_jobs: list[Job] | None = None
    child_contexts: list[JobContext] | None = None

    def __post_init__(self) -> None:
        """Validate the combination of directive and child fields.

        Raises:
            ValueError: If child_jobs or child_contexts are inconsistent
                with the directive.

        """
        if self.directive in {
            PipelineDirective.SPLIT_FOR_JOIN,
            PipelineDirective.SPLIT_WITHOUT_JOIN,
        }:
            if self.child_jobs is None:
                msg = f"StepResult with {self.directive} requires child_jobs"
                raise ValueError(msg)
            if self.child_contexts is None:
                msg = f"StepResult with {self.directive} requires child_contexts"
                raise ValueError(msg)
            if len(self.child_jobs) != len(self.child_contexts):
                msg = (
                    f"StepResult with {self.directive} requires child_jobs and "
                    f"child_contexts to have the same length"
                )
                raise ValueError(msg)
        elif self.child_jobs is not None or self.child_contexts is not None:
            msg = (
                f"StepResult with {self.directive} must not include "
                "child_jobs or child_contexts"
            )
            raise ValueError(msg)


class Step(ABC):
    """Abstract base class for pipeline steps with pre- and post-process hooks.

    A step represents a single stage in the execution pipeline. Each step
    defines *awaitable* pre-process and post-process methods. These methods
    are invoked sequentially by the PipelineExecutor.

    The step communicates pipeline control intent back to the PipelineExecutor
    via the StepResult returned from each hook.

    Notes:
        - `pre_process` runs **before** the job is sent to its main processing.
        - `post_process` runs **after** the job completes its main processing.
        - Fan-out (split), fan-in (join), and detach transitions are signaled
          by returning the appropriate PipelineDirective in StepResult.

    """

    @abstractmethod
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        """Run before the job is processed."""
        msg = "`pre_process` must be implemented in subclasses of Step"
        raise NotImplementedError(msg)

    @abstractmethod
    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        """Run after the job is processed."""
        msg = "`post_process` must be implemented in subclasses of Step"
        raise NotImplementedError(msg)

    async def join_jobs(
        self,
        gctx: GlobalContext,
        parent_jctx: JobContext,
        parent_job: Job,
        last_child: Job,
    ) -> None:
        """Merge the results of child jobs back into the parent job.

        Override this method in steps that return PipelineDirective.JOIN.
        """
        msg = (
            "`join_jobs` must be implemented in subclasses "
            "that return PipelineDirective.JOIN"
        )
        raise NotImplementedError(msg)
