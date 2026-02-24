from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import GlobalContext, JobContext
    from .model import Job


# =============================================================================
# Base Step Class
# =============================================================================


class Step(ABC):
    """Abstract base class for pipeline steps with pre- and post-process hooks.

    A step represents a single stage in the execution pipeline. Each step
    defines *awaitable* pre-process and post-process methods. These methods
    are invoked sequentially by the PipelineExecutor, unless the step
    explicitly triggers split or join behavior through mixins.

    Notes:
        - `pre_process` runs **before** the job is sent to its main processing.
        - `post_process` runs **after** the job completes its main processing.
        - Fan-out (split) and fan-in (join) transitions are handled by the
          pipeline executor based on marker mixins implemented by the step.

    """

    @abstractmethod
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Run before the job is processed."""
        msg = "`pre_process` must be implemented in subclasses of Step"
        raise NotImplementedError(msg)

    @abstractmethod
    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Run after the job is processed."""
        msg = "`post_process` must be implemented in subclasses of Step"
        raise NotImplementedError(msg)


# =============================================================================
# Split Mixins (Fan-out)
# =============================================================================


class SplitStepMixin:
    """Mixin marking that a step is capable of performing *split* behavior.

    A split is a fan-out operation: the parent job stops its pipeline
    progression, and multiple child jobs begin execution in parallel.

    Steps use this mixin to indicate that they *may* trigger a split.
    The actual creation of child jobs must be done inside the step's
    own `pre_process` or `post_process` method by assigning:

        job.children_jobs = [child1, child2, ...]

    Notes:
        This mixin does not define any abstract methods. The executor only
        checks whether the step is a split-capable step, together with the
        specific phase marker (SplitOnPreprocess or SplitOnPostprocess).

    """


class SplitOnPreprocess(SplitStepMixin):
    """Marker mixin indicating split behavior after pre-processing.

    This indicates that split behavior should be triggered
    **after `pre_process`** completes.

    Steps inheriting this mixin must set `job.children_jobs` during
    `pre_process`. The executor verifies that this mixin is present and
    performs the fan-out transition after the pre-process phase ends.

    A class inheriting this mixin must NOT also inherit JoinOnPreprocess.
    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit JoinOnPreprocess.

        Raises:
            TypeError: If the subclass also inherits JoinOnPreprocess.

        """
        super().__init_subclass__(**kwargs)

        # Disallow coexistence with JoinOnPreprocess
        if any(base is JoinOnPreprocess for base in cls.__bases__):
            message = (
                f"{cls.__name__} cannot inherit both "
                "SplitOnPreprocess and JoinOnPreprocess"
            )
            raise TypeError(message)


class SplitOnPostprocess(SplitStepMixin):
    """Marker mixin indicating split behavior after post-processing.

    This indicates that split behavior should be triggered
    **after `post_process`** completes.

    This is useful when child jobs can only be determined based on the
    results of post-processing or accumulated state.

    A class inheriting this mixin must NOT also inherit JoinOnPostprocess.
    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit JoinOnPostprocess.

        Raises:
            TypeError: If the subclass also inherits JoinOnPostprocess.

        """
        super().__init_subclass__(**kwargs)

        # Disallow coexistence with JoinOnPostprocess
        if any(base is JoinOnPostprocess for base in cls.__bases__):
            message = (
                f"{cls.__name__} cannot inherit both "
                "SplitOnPostprocess and JoinOnPostprocess"
            )
            raise TypeError(message)


# =============================================================================
# Join Mixins (Fan-in)
# =============================================================================


class JoinStepMixin(ABC):
    """Mixin marking that a step is capable of performing *join* behavior.

    A join is a fan-in operation: multiple child jobs complete in parallel,
    and once the **final child** finishes, the parent job resumes progression
    in the pipeline.

    Steps implementing this mixin must define a domain-specific `join_jobs`
    method. The PipelineExecutor performs join exactly once and only for
    the final child.

    Args passed to `join_jobs`:
        gctx:
            The global execution context.
        jctx:
            The parent job's JobContext.
        parent_job:
            The parent job that will resume execution.
        last_child:
            The final child job whose completion triggered the join.

    Notes:
        All child jobs can be accessed through:
            parent_job.children_jobs

    """

    @abstractmethod
    async def join_jobs(
        self,
        gctx: GlobalContext,
        parent_jctx: JobContext,
        parent_job: Job,
        last_child: Job,
    ) -> None:
        """Merge the results of child jobs back into the parent job."""
        raise NotImplementedError


class JoinOnPreprocess(JoinStepMixin):
    """Marker mixin indicating join behavior after pre-processing.

    This indicates that join behavior should be triggered
    **after `pre_process`** completes, but only for the final child job.

    A class inheriting this mixin must NOT also inherit SplitOnPreprocess.
    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit SplitOnPreprocess.

        Raises:
            TypeError: If the subclass also inherits SplitOnPreprocess.

        """
        super().__init_subclass__(**kwargs)

        # Disallow coexistence with SplitOnPreprocess
        if any(base is SplitOnPreprocess for base in cls.__bases__):
            message = (
                f"{cls.__name__} cannot inherit both "
                "JoinOnPreprocess and SplitOnPreprocess"
            )
            raise TypeError(message)


class JoinOnPostprocess(JoinStepMixin):
    """Marker mixin indicating join behavior timing.

    This mixin indicates that join behavior should be triggered
    **after `post_process`** completes, but only for the final child job.

    A class inheriting this mixin must NOT also inherit SplitOnPostprocess.
    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit SplitOnPostprocess.

        Raises:
            TypeError: If the subclass also inherits SplitOnPostprocess.

        """
        super().__init_subclass__(**kwargs)

        # Disallow coexistence with SplitOnPostprocess
        if any(base is SplitOnPostprocess for base in cls.__bases__):
            message = (
                f"{cls.__name__} cannot inherit both "
                "JoinOnPostprocess and SplitOnPostprocess"
            )
            raise TypeError(message)
