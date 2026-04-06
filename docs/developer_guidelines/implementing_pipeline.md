# Implementing Pipeline

This document explains how to implement custom steps for the OQTOPUS Engine pipeline.
It covers the lifecycle of steps, split/join mixins, JobContext usage, and best practices for writing reliable pipeline logic.

## 1. Overview

A pipeline is an ordered sequence of **steps** and **buffers**.
Steps transform jobs, interact with external services, and may control job flow through split or join semantics.

Each step may optionally define:

- `pre_process(job, jctx, gctx)`
- `post_process(job, jctx, gctx)`

The PipelineExecutor calls these methods depending on the current execution phase.

This guide explains:

- how to implement a standard step,
- how to implement split steps,
- how to implement join steps,
- what constraints apply when combining mixins,
- how `JobContext` is used across steps,
- how to use `PipelineDirective` to communicate execution intent to the engine,
- practical examples and best practices.

## 2. Writing a Standard Step

A standard step simply performs logic during pre-process and/or post-process phases.

```python
from oqtopus_engine_core.framework import Step

class MyStep(Step):
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        # pre-process phase logic

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        # post-process phase logic
```

Notes:

- You may implement only one of the two methods if needed.
- Avoid heavy I/O or unbounded background tasks inside a step.
- Steps should be stateless whenever possible.

## 3. Writing a Split Step

A split step expands a single parent job into one or more child jobs.

### 3.1 Split Mixins

- **SplitOnPreprocess**  
  Splitting occurs immediately after the step’s `pre_process()` execution.

- **SplitOnPostprocess**  
  Splitting occurs immediately after the step’s `post_process()` execution.

Across the entire pipeline execution, every `job_id` must be unique.  
During a split, the `PipelineExecutor` uses the parent’s `job_id` as the key for tracking whether all child jobs have reached the join point.  
Once **all** child jobs have passed through the join step, the `join_jobs()` method is invoked.

### 3.2 Implementing a Split Step

```python
from oqtopus_engine_core.framework import Step, SplitOnPreprocess
from oqtopus_engine_core.framework.context import link_parent_and_children

class MySplitStep(Step, SplitOnPreprocess):
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        child_jobs = []
        child_ctxs = []
        for index in range(2):
            # In a real implementation, you must specify all required fields for Job.
            c_job = Job(job_id=f"{job.job_id}-child{index}", job_type="sampling")
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        # Use the utility to establish bidirectional links between parent and children.
        # This replaces manual assignments to job.children and jctx.children.
        link_parent_and_children(jctx, job, child_ctxs, child_jobs)

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        # post-process phase logic
```

### 3.3 Framework Guarantees

The executor automatically:

- pauses parent execution at the split point,
- schedules child pipelines independently,
- resumes the parent only after join.

## 4. Writing a Join Step

A join step aggregates results from all child jobs and resumes the parent job.

### 4.1 Join Mixins

- **JoinOnPreprocess**  
  Joining occurs immediately after the step’s `pre_process()` execution.

- **JoinOnPostprocess**  
  Joining occurs immediately after the step’s `post_process()` execution.

### 4.2 Implementing a Join Step

```python
from oqtopus_engine_core.framework import Step, JoinOnPostprocess

class MyJoinStep(Step, JoinOnPostprocess):
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        # pre-process phase logic

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        # post-process phase logic

    async def join_jobs(
        self,
        gctx: GlobalContext,
        parent_jctx: JobContext,
        parent_job: Job,
        last_child: Job,
    ) -> None:
        # Perform any processing required for the join.
        # Example: update the parent job based on data collected from child jobs.
```

### 4.3 Framework Guarantees

- `join_jobs()` is called **exactly once**.
- It is executed **only by the last child** that reaches the join step.
- After join:
  - child jobs are released,
  - the parent job resumes traversal (forward or backward depending on join type).

## 5. Combining Split and Join Mixins

### 5.1 Valid and Invalid Combinations

For each phase (pre-process / post-process), a step may have **at most one**
control-flow mixin.

Invalid (TypeError):

- Multiple *pre-process* mixins:
  - `SplitOnPreprocess`
  - `JoinOnPreprocess`
  - `DetachOnPreprocess`

- Multiple *post-process* mixins:
  - `SplitOnPostprocess`
  - `JoinOnPostprocess`
  - `DetachOnPostprocess`

Valid:

- Any combination where mixins belong to **different phases**  
  (e.g., `JoinOnPreprocess` + `DetachOnPostprocess`)
- Steps without any control-flow mixin

If a step violates these constraints, the engine will raise a `TypeError` during class construction.

## 5.2 Dynamic Enabling/Disabling via JobContext

The executor reads four optional keys from `jctx` to gate split and join
execution at runtime.  For each operation the skip key is checked **before**
the enabled key.

| Key | Type | Effect when absent | Effect when present |
|---|---|---|---|
| `split_skip_steps` | `set[str]` | No steps skipped | Steps whose `__class__.__name__` is in the set are **never** split |
| `split_enabled_steps` | `set[str]` | All splits are executed (default) | Only steps whose `__class__.__name__` is in the set are split |
| `join_skip_steps` | `set[str]` | No steps skipped | Steps whose `__class__.__name__` is in the set are **never** joined |
| `join_enabled_steps` | `set[str]` | All joins are executed (default) | Only steps whose `__class__.__name__` is in the set are joined |

### Precedence rules

When both the skip key and the enabled key are present, the skip key takes
priority:

1. If the step's class name is in `split_skip_steps` → split is **disabled**
   (regardless of `split_enabled_steps`).
2. Otherwise, if `split_enabled_steps` is absent → split is **enabled**.
3. Otherwise → split is enabled only if the class name is in
   `split_enabled_steps`.

The same three-step rule applies to `join_skip_steps` / `join_enabled_steps`.

### Usage example

```python
# Disable all splits and joins for this job.
jctx = JobContext(initial={
    "split_enabled_steps": set(),
    "join_enabled_steps": set(),
})

# Enable split only for MySplitStep.
jctx = JobContext(initial={
    "split_enabled_steps": {"MySplitStep"},
})

# Skip split for a specific step while keeping all others enabled.
jctx = JobContext(initial={
    "split_skip_steps": {"MySplitStep"},
})

# Skip join for a specific step while keeping all others enabled.
jctx = JobContext(initial={
    "join_skip_steps": {"MyJoinStep"},
})

# skip takes priority: MySplitStep is skipped even though it appears
# in split_enabled_steps.
jctx = JobContext(initial={
    "split_skip_steps": {"MySplitStep"},
    "split_enabled_steps": {"MySplitStep", "OtherSplitStep"},
})
```

### Caveats

- **Split disabled, children created**: If a step's `pre_process` or
  `post_process` populates `job.children` / `jctx.children` but the split
  is disabled, those children are silently ignored.  Cleaning them up is
  the responsibility of the step implementation.

- **Join enabled without a preceding split**: If `join_enabled_steps`
  contains a step name but no split has occurred (so the root job reaches
  the join step directly), the executor will raise a `RuntimeError` because
  `job.parent` is `None`.  This is expected behavior and must be avoided by
  ensuring that split and join configuration are always consistent.

## 6. JobContext Usage

### 6.1 How JobContext Participates in Split/Join

- Both `job` and `jctx` form **parent/children trees** during split.
- Step implementations should treat each child JobContext independently.

### 6.2 Step History Recording

The executor writes execution history to:

```python
jctx.step_history: list[tuple[str, int]]
```

Each tuple is:

```python
(phase, cursor)
```

- `phase`: `"pre-process"` or `"post-process"`
- `cursor`: the 0-based pipeline index

Example:

```text
[("pre-process", 0), ("pre-process", 1), ("post-process", 1), ("post-process", 0)]
```

Child jobs maintain **their own** independent history.

After a join, the parent resumes and continues writing new `(phase, cursor)` entries.

#### Example: Parent and Child Histories

```text
Parent jctx.step_history:
    ("pre-process", 0), ("pre-process", 1)

Parent stops and children begin after the split:
Children jctx.step_history:
    ("pre-process", 2), ("post-process", 2), ("post-process", 1)

Parent resumes and children stop after the join:
Parent jctx.step_history:
    ("post-process", 0)
```

Final accumulated histories:

- **Parent**:  
  `[("pre-process", 0), ("pre-process", 1), ("post-process", 0)]`

- **Children**:  
  `[("pre-process", 2), ("post-process", 2), ("post-process", 1)]`

## 7. PipelineDirective

### 7.1 Overview

`PipelineDirective` is an enum that a step can set on `jctx` to change the pipeline executor's default behaviour **after** the current step completes.
It provides a lightweight, one-shot channel from a step implementation to the pipeline executor.

```python
from oqtopus_engine_core.framework import PipelineDirective
```

Available values:

| Value | Effect |
| ----- | ------ |
| `PipelineDirective.NONE` | Default — no change to executor behaviour |
| `PipelineDirective.IGNORE_SPLIT_TRACKING` | After splitting, skip registration of the pending-children counter for the parent job |

The directive is **consumed once**: the executor resets it to `NONE` immediately after acting on it, so it does not affect any subsequent split.

### 7.2 `IGNORE_SPLIT_TRACKING`

Use this directive when a step performs a split but **no corresponding join is expected**.
Without it, the executor registers a `_pending_children` counter for the parent job and waits for a join that will never arrive, causing a memory leak and the parent job hanging indefinitely.

#### When to use

- The step spreads results to child jobs in its `pre_process()` or `post_process()` and then allows the children to complete their own pipelines independently.
- No `JoinOn*` mixin is present downstream for these children.

#### How to use

Set the directive **inside** the step method that also creates the child jobs, before returning:

```python
from oqtopus_engine_core.framework import PipelineDirective, Step, SplitOnPostprocess
from oqtopus_engine_core.framework.context import link_parent_and_children

class MySplitWithoutJoinStep(Step, SplitOnPostprocess):
    async def pre_process(self, gctx, jctx, job):
        pass

    async def post_process(self, gctx, jctx, job):
        # Signal to the executor: do not register a pending-children counter.
        jctx.pipeline_directive = PipelineDirective.IGNORE_SPLIT_TRACKING

        child_jobs = []
        child_ctxs = []
        for i in range(len(job.children)):
            c_job = ...  # populate child Job fields
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        link_parent_and_children(jctx, job, child_ctxs, child_jobs)
```

#### Guarantees

- When `IGNORE_SPLIT_TRACKING` is active the executor **still** performs the split and starts all child pipelines.
- The only thing skipped is the `_pending_children` counter update — the parent job will not wait for a join.
- The directive is reset to `NONE` after the split regardless of whether it was acted on or not.

### 7.3 `pipeline_directive` as a Reserved Attribute

`pipeline_directive` is stored as a **Python attribute** on `JobContext`, not inside the underlying data dictionary.
This means it is never serialised with the rest of the context and is always re-initialised to `NONE` for each new `JobContext`.

```python
jctx = JobContext()
print(jctx.pipeline_directive)          # PipelineDirective.NONE
print("pipeline_directive" in jctx)     # False  (not in the data dict)
```

Like `parent` and `children`, it **cannot** be deleted:

```python
del jctx.pipeline_directive  # raises AttributeError
```

## 8. Best Practices

### Do

- Keep child jobs independent and self-contained.
- Use JobContext to store per-child metadata.
- Clearly indicate split or join behavior in class names.
- Keep `join_jobs()` idempotent and aggregation-only.
- Use `PipelineDirective.IGNORE_SPLIT_TRACKING` when performing a split that has no corresponding join.

### Do Not

- Spawn unmanaged background tasks.
- Perform blocking I/O inside a step.
- Leave `pipeline_directive` set to a non-`NONE` value after the step — the executor resets it automatically, but relying on that for control flow is an anti-pattern.

## 9. Summary

To implement steps correctly in the OQTOPUS Engine:

- choose the appropriate mixins (split/join, pre/post),
- implement only the necessary lifecycle methods,
- let the executor handle concurrency and synchronization,
- use `PipelineDirective` when a step needs to alter the executor's default split-tracking behaviour,
- keep logic simple, deterministic, and phase-appropriate.

This structured model ensures predictable behavior across parallel execution paths and allows complex quantum job workflows to be expressed cleanly.
