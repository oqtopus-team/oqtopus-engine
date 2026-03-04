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

        # When implementing a split step manually, you must explicitly assign
        # the children to both job.children and jctx.children.
        job.children = child_jobs
        jctx.children = child_ctxs

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

- sets the parent attribute on both the child job and child JobContext
  (establishing a bidirectional link between the parent job and each child),
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

## 7. Best Practices

### Do

- Keep child jobs independent and self-contained.
- Use JobContext to store per-child metadata.
- Clearly indicate split or join behavior in class names.
- Keep `join_jobs()` idempotent and aggregation-only.

### Do Not

- Spawn unmanaged background tasks.
- Perform blocking I/O inside a step.

## 8. Summary

To implement steps correctly in the OQTOPUS Engine:

- choose the appropriate mixins (split/join, pre/post),
- implement only the necessary lifecycle methods,
- let the executor handle concurrency and synchronization,
- keep logic simple, deterministic, and phase-appropriate.

This structured model ensures predictable behavior across parallel execution paths and allows complex quantum job workflows to be expressed cleanly.
