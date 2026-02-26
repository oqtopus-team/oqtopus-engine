from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import GlobalContext, JobContext
    from .model import Job


# =============================================================================
# Phase-exclusive mixin groups
# =============================================================================
# NOTE:
#   These lists are intentionally initialized as *empty lists*.
#   They will be populated LATER (at the bottom of this file) *after* all mixin
#   classes such as SplitOnPreprocess, JoinOnPreprocess, DetachOnPreprocess, etc.
#   have been defined.
#
#   We DO NOT assign the final list here because the classes referenced inside
#   them are not yet defined at this stage. Instead, we overwrite the contents
#   of the lists later using slice-assignment (`[:] =`), so the list object
#   itself remains the same.
#
#   Keeping the same list object is safer: if any other module imports these
#   constants before the end of this file, they will still refer to the same
#   list object whose contents are updated later.
#
#   Therefore:
#       - Define empty lists here
#       - Fill their contents later with PREPROCESS_EXCLUSIVE_MIXINS[:] = [...]
#
PREPROCESS_EXCLUSIVE_MIXINS: list[type] = []
POSTPROCESS_EXCLUSIVE_MIXINS: list[type] = []


# =============================================================================
# Shared validation utilities for Step mixins
# =============================================================================


def validate_exclusive_mixins(
    cls: type,
    phase_mixins: list[type],
) -> None:
    """Ensure that a class does not inherit multiple mixins for the same phase.

    Raises:
        TypeError: If the class inherits more than one mixin from the specified
                     phase_mixins list.

    """
    # Collect mixins of this phase that the class actually inherits
    inherited = [base for base in cls.__bases__ if base in phase_mixins]

    # If this class itself is a phase mixin, include it
    if cls in phase_mixins:
        inherited.append(cls)

    # If two or more phase mixins are present, that's a conflict
    if len(inherited) > 1:
        conflict_list = ", ".join(b.__name__ for b in inherited)
        message = (
            f"{cls.__name__} cannot inherit multiple mixins from the same phase: "
            f"{conflict_list}"
        )
        raise TypeError(message)


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

    A class inheriting this mixin must NOT also inherit any other
    preprocess-phase transition mixins (JoinOnPreprocess, DetachOnPreprocess,
    or another SplitOnPreprocess).

    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit JoinOnPreprocess.

        Raises:
            TypeError: If the subclass also inherits JoinOnPreprocess.

        """
        super().__init_subclass__(**kwargs)
        validate_exclusive_mixins(cls, PREPROCESS_EXCLUSIVE_MIXINS)


class SplitOnPostprocess(SplitStepMixin):
    """Marker mixin indicating split behavior after post-processing.

    This indicates that split behavior should be triggered
    **after `post_process`** completes.

    This is useful when child jobs can only be determined based on the
    results of post-processing or accumulated state.

    A class inheriting this mixin must NOT also inherit any other
    postprocess-phase transition mixins (SplitOnPostprocess,
    JoinOnPostprocess, or another DetachOnPostprocess).

    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit JoinOnPostprocess.

        Raises:
            TypeError: If the subclass also inherits JoinOnPostprocess.

        """
        super().__init_subclass__(**kwargs)
        validate_exclusive_mixins(cls, POSTPROCESS_EXCLUSIVE_MIXINS)


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

    A class inheriting this mixin must NOT also inherit any other
    preprocess-phase transition mixins (SplitOnPreprocess,
    JoinOnPreprocess, or another DetachOnPreprocess).

    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit SplitOnPreprocess.

        Raises:
            TypeError: If the subclass also inherits SplitOnPreprocess.

        """
        super().__init_subclass__(**kwargs)
        validate_exclusive_mixins(cls, PREPROCESS_EXCLUSIVE_MIXINS)


class JoinOnPostprocess(JoinStepMixin):
    """Marker mixin indicating join behavior timing.

    This mixin indicates that join behavior should be triggered
    **after `post_process`** completes, but only for the final child job.

    A class inheriting this mixin must NOT also inherit any other
    postprocess-phase transition mixins (SplitOnPostprocess,
    JoinOnPostprocess, or another DetachOnPostprocess).

    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate that subclasses do not also inherit SplitOnPostprocess.

        Raises:
            TypeError: If the subclass also inherits SplitOnPostprocess.

        """
        super().__init_subclass__(**kwargs)
        validate_exclusive_mixins(cls, POSTPROCESS_EXCLUSIVE_MIXINS)


# =============================================================================
# Detach Mixins (Async boundary)
# =============================================================================


class DetachStepMixin:
    """Mixin marking that a step introduces an async *detach* boundary.

    A detach is an execution boundary: after a specific phase completes,
    the remaining pipeline progression for the same job is resumed in a
    separate coroutine.

    This allows the current worker loop to return early (e.g., to fetch
    another job from a buffer) while the remainder of the pipeline
    continues asynchronously.

    Notes:
        - This mixin does not define any abstract methods.
        - The PipelineExecutor checks for this mixin together with
          phase-specific markers (DetachOnPreprocess or
          DetachOnPostprocess).
        - Detach behavior must be coordinated by the executor;
          this mixin only serves as a marker.

    """


class DetachOnPreprocess(DetachStepMixin):
    """Marker mixin indicating detach behavior after pre-processing.

    This indicates that the pipeline should be detached into a new coroutine
    **after `pre_process`** completes.

    The current worker must stop progressing the pipeline further and
    return control to the worker loop. The executor is responsible for
    scheduling the continuation of the pipeline from the next step
    in a separate coroutine.

    A class inheriting this mixin must NOT also inherit any other
    preprocess-phase transition mixins (SplitOnPreprocess,
    JoinOnPreprocess, or another DetachOnPreprocess).

    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate preprocess-phase mutual exclusivity.

        Raises:
            TypeError:
                If the subclass inherits multiple preprocess-phase
                transition mixins simultaneously.

        """
        super().__init_subclass__(**kwargs)
        validate_exclusive_mixins(cls, PREPROCESS_EXCLUSIVE_MIXINS)


class DetachOnPostprocess(DetachStepMixin):
    """Marker mixin indicating detach behavior after post-processing.

    This indicates that the pipeline should be detached into a new coroutine
    **after `post_process`** completes.

    This is useful when a step must complete its post-processing logic
    synchronously but wishes to allow the remaining pipeline execution
    to proceed asynchronously.

    A class inheriting this mixin must NOT also inherit any other
    postprocess-phase transition mixins (SplitOnPostprocess,
    JoinOnPostprocess, or another DetachOnPostprocess).

    The mutual exclusivity is enforced via __init_subclass__.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Validate postprocess-phase mutual exclusivity.

        Raises:
            TypeError:
                If the subclass inherits multiple postprocess-phase
                transition mixins simultaneously.

        """
        super().__init_subclass__(**kwargs)
        validate_exclusive_mixins(cls, POSTPROCESS_EXCLUSIVE_MIXINS)


# =============================================================================
# Populate phase-exclusive mixin groups
# =============================================================================
# NOTE:
#   At this point in the file, all mixin classes (SplitOnPreprocess,
#   JoinOnPreprocess, DetachOnPreprocess, etc.) have been defined.
#
#   Now we populate the exclusive mixin groups with slice-assignment (`[:] =`).
#
#   Why slice-assignment?
#   - It replaces the *contents* of the existing list object
#     instead of rebinding the name to a new list.
#   - This ensures that any external module holding a reference to this
#     list (via `from step import PREPROCESS_EXCLUSIVE_MIXINS`) will see
#     the updated contents.
#
#   Conceptual example:
#   - Normal assignment creates a new list object (unsafe)
#   - Slice assignment (`[:]`) updates the existing list object (safe)
#
#   Therefore, we use `[:] =` to preserve object identity and avoid subtle bugs.
#
PREPROCESS_EXCLUSIVE_MIXINS[:] = [
    SplitOnPreprocess,
    JoinOnPreprocess,
    DetachOnPreprocess,
]

POSTPROCESS_EXCLUSIVE_MIXINS[:] = [
    SplitOnPostprocess,
    JoinOnPostprocess,
    DetachOnPostprocess,
]
