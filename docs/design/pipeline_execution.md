# Pipeline Execution

This document describes the internal execution model of the OQTOPUS Engine Core pipeline.  
It explains in detail how the pipeline traverses its elements, how buffers and workers provide concurrency, and how structured parallelism is achieved through split and join steps.

## 1. Overview

The pipeline execution model is based on three key principles:

1. **Two-phase traversal**:  
   - pre-process phase (forward direction)  
   - post-process phase (backward direction)

2. **Asynchronous buffering**:  
   - buffers act as boundaries between execution segments;  
   - each buffer has its own worker task.

3. **Structured parallelism**:  
   - split steps create multiple child jobs;  
   - child jobs run independently;  
   - join steps re-synchronize and resume the parent job.

The PipelineExecutor orchestrates all of these behaviors.

## 2. Pipeline Construction from `config.yaml`

## 2.1 Defining the Pipeline in `config.yaml`

OQTOPUS Engine Core supports **configuration-driven pipeline construction**.
A pipeline does not need to be hard-coded in Python—the engine can create a
`PipelineExecutor` directly from the YAML configuration.

The `pipeline_executor` section defines:

- the ordered list of pipeline elements,
- which component acts as the job buffer,
- which component handles exceptions during pipeline execution.

A corresponding entry in `di_container.registry` supplies the concrete
implementations for each name.

Example:

```yaml
pipeline_executor:
  pipeline:
    - job_repository_update_step
    - multi_manual_step
    - tranqu_step
    - estimator_step
    - ro_error_mitigation_step
    - buffer
    - sse_step
    - device_gateway_step
  job_buffer: buffer
  exception_handler: pipeline_exception_handler

di_container:
  registry:
    job_repository_update_step:
      _target_: oqtopus_engine_core.steps.JobRepositoryUpdateStep

    ...
```

### 2.2 How the Engine Uses This Configuration

The engine reads `pipeline_executor` and retrieves all components from the dependency-injection container:

- every pipeline element (steps and buffers) is retrieved from the DI registry;
- the element order in YAML becomes the actual traversal order;
- the job buffer is assigned to `job_buffer`;
- the exception handler is assigned to `exception_handler`.

Internally, the engine uses `PipelineBuilder.build()` to construct the executor:

- read the ordered list under `pipeline`;
- retrieve each component via `dicon.get(name)`;
- retrieve `job_buffer` and `exception_handler`;
- create a `PipelineExecutor` with these objects.

This allows:

- fully declarative pipeline definition,
- environment-specific override via YAML or environment variables,
- consistent dependency management across all components,
- a clean separation between "pipeline construction" and "pipeline execution."

The following sections describe **how the constructed pipeline executes jobs** including two-phase traversal, buffers, workers, split/join semantics, detach, and error handling.

## 3. Pipeline Structure

A pipeline consists of an ordered sequence of **elements**:

- **Step**: performs a transformation or operation on a job.
- **Buffer**: queues jobs and transfers control to a separate worker.

Example:

```text
Step A → Step B → Buffer X → Step C → Step D
```

Jobs advance through these elements depending on the current phase of execution.

## 4. Two-Phase Traversal

The executor performs job traversal in two distinct phases:

### 4.1 pre-process Phase (Forward Traversal)

- Traversal starts at index 0.
- For each step, the executor calls `pre_process(job, jctx)`.
- When reaching a buffer:
  - the job is pushed into the buffer's queue,
  - the forward traversal halts,
  - the buffer’s worker later resumes execution from the next element.

This phase is responsible for:

- preparing job metadata,
- performing transformations or expansions (e.g., splitting),
- interacting with external services before execution.

### 4.2 post-process Phase (Backward Traversal)

- Traversal starts at the end of the pipeline.
- For each step, the executor calls `post_process(job, jctx)`.
- Buffers are skipped in this direction.
- This phase begins when:
  - a job reaches the final element of the pipeline, or
  - a parent job resumes after its children have been joined.

The backward phase is typically used for:

- collecting results,
- cleanup actions,
- applying final transforms,
- merging metadata into the job.

## 5. Buffers and Worker Tasks

Buffers create asynchronous execution boundaries allowing controlled parallelism.

### 5.1 Enqueuing and Halting

During the **pre-process phase**, when a job reaches a buffer:

- the executor enqueues `(gctx, jctx, job)` into the buffer, and
- **the current pre-process traversal for that job stops at the buffer**.

The remaining pre-process steps **are not executed in the same traversal call**.  
Instead, the pipeline will resume pre-process traversal for that job later,
driven by one of the buffer workers (see next section).

The pipeline as a whole continues running:  
other jobs may proceed, other buffers may activate workers, and detached steps
may continue to run in the background.

### 5.2 Worker Execution and Concurrency

Each buffer exposes a `max_concurrency` property.

- `max_concurrency` defines how many worker tasks may consume jobs
  from this buffer concurrently.
- When the pipeline starts, the executor spawns exactly
  `buffer.max_concurrency` workers per buffer.

Each worker repeatedly performs:

1. `gctx, jctx, job = await buffer.get()`
2. resumes **pre-process traversal** starting from the element *after the buffer*.

Workers continue this loop until the executor is stopped.

If a step implements `DetachOnPreprocess` or `DetachOnPostprocess`,
the caller regains control immediately while the detached coroutine continues
in the background, but the buffer consumption pattern (get → resume pre-process)
remains unchanged.

This design provides:

- configurable parallelism per buffer,
- scalable throughput,
- queue-based backpressure,
- asynchronous decoupling inside the pre-process phase.

### 5.3 Configuration (QueueBuffer Example)

When using `QueueBuffer`, its concurrency can be configured in `config.yaml`:

```yaml
pipeline_executor:
  pipeline:
    - buffer          # refers to the entry below

  job_buffer: buffer

di_container:
  registry:
    buffer:
      _target_: oqtopus_engine_core.buffers.QueueBuffer
      maxsize: 0           # optional, unlimited queue
      max_concurrency: 3   # spawn 3 workers for this buffer
```

## 6. Split Execution

### 6.1 Purpose of Split Steps

A split step divides one job into multiple independent child jobs.  
This enables functionalities such as:

- multi-programming,
- fan-out computation,
- job replication for sampling or batching,
- custom branching logic.

### 6.2 How Splitting Works

Depending on the type of step:

- **SplitOnPreprocess**: splitting occurs during pre-process traversal.
- **SplitOnPostprocess**: splitting occurs during post-process traversal.

When a split occurs:

1. The parent job's traversal **pauses**.
2. New child jobs and contexts are created and placed in `job.children` and `jctx.children`.
3. Each child job starts its own execution:
   - traversal starts from the first pipeline element,
   - workers, buffers, and phases apply independently.
4. Parent job waits until children complete.

The executor automatically manages:

- parent/child relationships,
- tracking of remaining children,
- dispatching each child into the pipeline.

### 6.3 Child Job Independence

Each child job:

- has its own JobContext,
- follows the same pipeline structure,
- enqueues into buffers independently,
- performs its own pre-process and post-process phases,
- may itself be split further (tree recursion).

This creates a full job tree.

## 7. Join Execution

### 7.1 Purpose of Join Steps

Join steps re-synchronize parallel execution paths.  
They aggregate child results to resume the parent job.

Typical use cases:

- merging multiple circuit executions,
- combining measurement results,
- collecting metadata or logs,
- post-processing on aggregated outputs.

### 7.2 How Join Works

A join step is executed when the **last** living child job reaches it.

The executor detects when:

- all children have finished their pipeline traversal up to the join step,
- no child remains in flight,
- the pending-children counter reaches zero.

At that moment:

1. `join_jobs(global_ctx, child_ctx, parent_job, last_child)` is called **exactly once**.
2. Only the class designated as a join step will receive this callback.
3. After the join:
   - child jobs are released,
   - the parent job resumes traversal.
4. The parent continues processing from the join step onward in the relevant phase.

This ensures:

- deterministic timing of joins,
- no duplicate join invocations,
- safe synchronization before parent continues.

### 7.3 Join During Forward vs Backward Traversal

Join steps may live in the pipeline during pre-process or post-process phases:

- **JoinOnPreprocess**: parent resumes from the join point during forward traversal.
- **JoinOnPostprocess**: parent resumes during backward traversal.

This enables expressive control-flow patterns.

## 8. Composite Job Trees and Structured Concurrency

Split/Join semantics create a structured execution pattern analogous to classical fork/join models:

1. pre-process: encounter split → spawn children → children run concurrently
2. children: advance through steps and buffers independently
3. join: the last child triggers the join → aggregation happens here
4. parent: resumes either forward or backward traversal
5. post-process: eventually the root completes backward traversal

This ensures:

- predictable order of events,
- structured concurrency,
- clear control of job lifecycle,
- safe aggregation points.

## 9. Detach Execution

Detach steps allow part of a job’s pipeline traversal to continue in a
separate coroutine.  
This enables the worker to immediately return to its buffer loop,
improving throughput while preserving the pipeline’s two-phase semantics.

A step may detach at:

- **pre-process phase** (`DetachOnPreprocess`)
- **post-process phase** (`DetachOnPostprocess`)

When a detach occurs:

1. The executor spawns a background task that continues traversal from
   the next (or previous) pipeline index.
2. The detached task is tracked in `background_tasks` and is cleaned up
   when finished.
3. The current worker returns immediately, allowing the buffer to fetch
   more jobs.

Detach does *not* replace split/join semantics and does not create child
jobs.  
It simply moves the remaining traversal of the same job into a new
coroutine.

Detaching preserves:

- the same JobContext and Job instance,
- the same two-phase traversal,
- the same correctness guarantees as non-detached execution.

This mechanism is useful for offloading long-running pipeline segments
without requiring additional worker threads.

## 10. Exception Handling

Each step is executed in a “safe call” wrapper:

- exceptions are logged with job identifiers and step names;
- errors in children prevent join operations;
- parent jobs do not resume if a join cannot occur;
- exceptions do not silently propagate across unrelated job executions.

This prevents inconsistent pipeline states and provides clear diagnostics.

## 11. Step History Tracking

Each job maintains an execution history stored inside its `JobContext`
under the field `step_history`.  
This history records **which step was executed**, **in which phase**, and
**at which pipeline index** during traversal.

### 11.1 Representation

`step_history` is a **list of tuples** of the form: `(phase, cursor)`

where:

- **phase**: `"pre-process"` or `"post-process"`
- **cursor**:  
  the integer index (0-based) of the pipeline element  
  that was executed during that phase

Example:

```python
jctx.step_history == [
    ("pre-process", 0),   # Step at index 0 executed in pre-process phase
    ("pre-process", 1),   # Step at index 1 executed in pre-process phase
    ("post-process", 1),  # Step at index 1 executed in post-process phase
    ("post-process", 0),  # Step at index 0 executed in post-process phase
]
```

This structure provides a precise trace of the job’s movement through the pipeline.

### 11.2 Parent and Child Jobs

When a split step creates child jobs:

- The parent job's step_history stops growing at the point of the split.
- Each child job receives its own JobContext and begins recording its
  own independent step history list.
- After the join step, the parent job resumes and continues appending new
  (phase, cursor) entries to its original list.

This results in:

- one history list for the parent job, and
- separate, independent history lists for each child job.

Example (conceptual):

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

## 12. Summary

The pipeline execution model supports:

- two-phase deterministic traversal,
- asynchronous concurrency via buffers,
- structured parallelism through split and join steps,
- job trees with predictable synchronization,
- robust handling of errors.

This design balances flexibility for pipeline authors with a strong consistency model for parallel execution of quantum jobs.
