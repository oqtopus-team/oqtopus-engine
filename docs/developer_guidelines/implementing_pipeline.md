# Implementing Pipeline

This document explains how to implement custom steps for the OQTOPUS Engine pipeline.
It covers the lifecycle of steps, how to express split/join/detach control flow through
`StepResult`, JobContext usage, and best practices for writing reliable pipeline logic.

## 1. Overview

A pipeline is an ordered sequence of **steps** and **buffers**.
Steps transform jobs, interact with external services, and may control job flow through
split, join, or detach semantics.

Each step may optionally define:

- `pre_process(gctx, jctx, job) -> StepResult`
- `post_process(gctx, jctx, job) -> StepResult`

The PipelineExecutor calls these methods depending on the current execution phase,
and inspects the returned `StepResult` to determine whether to split, join, detach, or
continue normally.

This guide explains:

- how to implement a standard step,
- how to implement split steps,
- how to implement join steps,
- how `JobContext` is used across steps,
- how to use `PipelineDirective` to communicate execution intent to the engine,
- practical examples and best practices.

## 2. Writing a Standard Step

A standard step simply performs logic during pre-process and/or post-process phases.

```python
from oqtopus_engine_core.framework import Step, StepResult

class MyStep(Step):
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        # pre-process phase logic
        return StepResult()

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        # post-process phase logic
        return StepResult()
```

Notes:

- You may implement only one of the two methods if needed.
- A plain `StepResult()` (no arguments) means "continue normally" — no split, join, or detach.
- Avoid heavy I/O or unbounded background tasks inside a step.
- Steps should be stateless whenever possible.

## 3. Writing a Split Step

A split step expands a single parent job into one or more child jobs by returning a
`StepResult` with a split directive.

### 3.1 Split with Join (`SPLIT_FOR_JOIN`)

Use this directive when child jobs are expected to be collected at a downstream join step.

```python
from oqtopus_engine_core.framework import Step, StepResult
from oqtopus_engine_core.framework.step import PipelineDirective

class MySplitStep(Step):
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        child_jobs = []
        child_ctxs = []
        for index in range(2):
            # In a real implementation, specify all required fields for Job.
            c_job = Job(job_id=f"{job.job_id}-child{index}", ...)
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        return StepResult(
            directive=PipelineDirective.SPLIT_FOR_JOIN,
            child_jobs=child_jobs,
            child_contexts=child_ctxs,
        )

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        return StepResult()
```

The executor automatically:

- establishes parent/child links between jobs and contexts,
- pauses parent execution at the split point,
- schedules child pipelines independently,
- resumes the parent only after all children have reached the join step.

### 3.2 Split without Join (`SPLIT_WITHOUT_JOIN`)

Use this directive when child jobs are expected to complete their own pipelines
independently, with no corresponding join.

```python
from oqtopus_engine_core.framework import Step, StepResult
from oqtopus_engine_core.framework.step import PipelineDirective

class MySplitWithoutJoinStep(Step):
    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        child_jobs = []
        child_ctxs = []
        for i in range(len(some_list)):
            c_job = ...
            c_jctx = JobContext(initial={})
            child_jobs.append(c_job)
            child_ctxs.append(c_jctx)

        return StepResult(
            directive=PipelineDirective.SPLIT_WITHOUT_JOIN,
            child_jobs=child_jobs,
            child_contexts=child_ctxs,
        )
```

With `SPLIT_WITHOUT_JOIN`, the executor still starts all child pipelines, but the parent
job does **not** wait for them. No `_pending_children` counter is registered, so the
parent proceeds (or finishes) immediately.

### 3.3 Framework Guarantees

Regardless of which split directive is used, the executor:

- calls `link_parent_and_children` internally — the step must **not** call it directly,
- dispatches each child job into the pipeline from the beginning,
- manages the `_pending_children` counter for `SPLIT_FOR_JOIN` only.

## 4. Writing a Join Step

A join step aggregates results from all child jobs and signals to the executor that
the current child has completed its contribution.

### 4.1 Signaling Join Intent

A step signals join intent by returning `StepResult(directive=PipelineDirective.JOIN)`
from its `pre_process()` or `post_process()` method **when the job has a parent**.

```python
from oqtopus_engine_core.framework import Step, StepResult
from oqtopus_engine_core.framework.step import PipelineDirective

class MyJoinStep(Step):
    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        return StepResult()

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        if job.parent is not None:
            return StepResult(directive=PipelineDirective.JOIN)
        return StepResult()

    async def join_jobs(
        self,
        gctx: GlobalContext,
        parent_jctx: JobContext,
        parent_job: Job,
        last_child: Job,
    ) -> None:
        # Aggregate child results into the parent job.
        # This is called exactly once, by the last child to arrive.
        pass
```

The `JOIN` directive instructs the executor to:

1. Decrement the parent's pending-children counter.
2. If the counter reaches zero, call `join_jobs()` and resume the parent job.
3. If the counter is still nonzero, simply finish this child's traversal.

### 4.2 Framework Guarantees

- `join_jobs()` is called **exactly once**, by the last child to reach the join step.
- After join:
  - child jobs are released,
  - the parent job resumes traversal from the join step onward (forward or backward,
    depending on whether `JOIN` was signaled from `pre_process` or `post_process`).

## 5. JobContext Usage

### 5.1 How JobContext Participates in Split/Join

- Both `job` and `jctx` form **parent/child trees** during split (established by the
  executor, not by the step).
- Step implementations should treat each child JobContext independently.

### 5.2 Step History Recording

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

## 6. PipelineDirective

### 6.1 Overview

`PipelineDirective` is a `StrEnum` returned inside `StepResult` to communicate
execution intent from a step to the pipeline executor.

```python
from oqtopus_engine_core.framework.step import PipelineDirective
```

Available values:

| Value | Effect |
| ----- | ------ |
| `PipelineDirective.NONE` | Default — continue normally with no change to executor behaviour |
| `PipelineDirective.SPLIT_FOR_JOIN` | Split parent into child jobs; register a pending-children counter; resume parent after join |
| `PipelineDirective.SPLIT_WITHOUT_JOIN` | Split parent into child jobs; do **not** register a pending-children counter; parent does not wait |
| `PipelineDirective.JOIN` | Decrement the parent's pending-children counter; if zero, call `join_jobs()` and resume the parent |
| `PipelineDirective.DETACH` | Continue remaining traversal in a background coroutine; return the worker to the buffer loop immediately |

### 6.2 Usage Pattern

Always return a `StepResult` from `pre_process()` and `post_process()`.
Use a plain `StepResult()` for normal execution:

```python
return StepResult()                              # NONE — continue normally

return StepResult(                               # split with join
    directive=PipelineDirective.SPLIT_FOR_JOIN,
    child_jobs=child_jobs,
    child_contexts=child_ctxs,
)

return StepResult(                               # split without join
    directive=PipelineDirective.SPLIT_WITHOUT_JOIN,
    child_jobs=child_jobs,
    child_contexts=child_ctxs,
)

return StepResult(directive=PipelineDirective.JOIN)      # signal join
return StepResult(directive=PipelineDirective.DETACH)    # detach traversal
```

## 7. Best Practices

### Do

- Keep child jobs independent and self-contained.
- Use JobContext to store per-child metadata.
- Clearly indicate split or join behaviour in class names.
- Keep `join_jobs()` idempotent and aggregation-only.
- Use `SPLIT_WITHOUT_JOIN` when performing a split that has no corresponding join.
- Return `StepResult(directive=PipelineDirective.JOIN)` only when `job.parent is not None`.

### Do Not

- Call `link_parent_and_children()` inside a step — the executor calls it automatically.
- Spawn unmanaged background tasks.
- Perform blocking I/O inside a step.
- Set `child_jobs` or `child_contexts` on `StepResult` when the directive is not a split
  directive — those fields are ignored and may cause confusion.

## 8. Summary

To implement steps correctly in the OQTOPUS Engine:

- return `StepResult` from every `pre_process()` and `post_process()`,
- use `PipelineDirective` inside `StepResult` to signal split, join, or detach intent,
- implement `join_jobs()` on any step that signals `SPLIT_FOR_JOIN` in the pipeline,
- let the executor handle parent/child linking, concurrency, and synchronization,
- keep logic simple, deterministic, and phase-appropriate.

This structured model ensures predictable behaviour across parallel execution paths and
allows complex quantum job workflows to be expressed cleanly.
