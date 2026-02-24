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

## 2. Pipeline Structure

A pipeline consists of an ordered sequence of **elements**:

- **Step**: performs a transformation or operation on a job.
- **Buffer**: queues jobs and transfers control to a separate worker.

Example:

```text
Step A → Step B → Buffer X → Step C → Step D
```

Jobs advance through these elements depending on the current phase of execution.

## 3. Two-Phase Traversal

The executor performs job traversal in two distinct phases:

### 3.1 pre-process Phase (Forward Traversal)

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

### 3.2 post-process Phase (Backward Traversal)

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

## 4. Buffers and Worker Tasks

Buffers create asynchronous execution boundaries.

### 4.1 Enqueuing and Halting

During pre-process traversal:

- when a job hits a buffer, the executor posts it into the buffer queue;
- the executor returns control immediately without traversing downstream elements.

### 4.2 Worker Execution

Each buffer has a dedicated worker, started when the pipeline begins.  
A worker performs:

1. `job = buffer.get()`
2. resumes pipeline traversal from *after the buffer* in the pre-process phase.

This design allows:

- controlled concurrency (multiple buffers = multiple workers),
- scalable throughput,
- natural backpressure (queue length control),
- asynchronous decoupling between pipeline segments.

### 4.3 Buffers in post-process Phase

Buffers are ignored during backward traversal.  
Jobs do **not** stop or queue in the post-process phase.

## 5. Split Execution

### 5.1 Purpose of Split Steps

A split step divides one job into multiple independent child jobs.  
This enables functionalities such as:

- multi-programming,
- fan-out computation,
- job replication for sampling or batching,
- custom branching logic.

### 5.2 How Splitting Works

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

### 5.3 Child Job Independence

Each child job:

- has its own JobContext,
- follows the same pipeline structure,
- enqueues into buffers independently,
- performs its own pre-process and post-process phases,
- may itself be split further (tree recursion).

This creates a full job tree.

## 6. Join Execution

### 6.1 Purpose of Join Steps

Join steps re-synchronize parallel execution paths.  
They aggregate child results to resume the parent job.

Typical use cases:

- merging multiple circuit executions,
- combining measurement results,
- collecting metadata or logs,
- post-processing on aggregated outputs.

### 6.2 How Join Works

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

### 6.3 Join During Forward vs Backward Traversal

Join steps may live in the pipeline during pre-process or post-process phases:

- **JoinOnPreprocess**: parent resumes from the join point during forward traversal.
- **JoinOnPostprocess**: parent resumes during backward traversal.

This enables expressive control-flow patterns.

## 7. Composite Job Trees and Structured Concurrency

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

## 8. Exception Handling

Each step is executed in a “safe call” wrapper:

- exceptions are logged with job identifiers and step names;
- errors in children prevent join operations;
- parent jobs do not resume if a join cannot occur;
- exceptions do not silently propagate across unrelated job executions.

This prevents inconsistent pipeline states and provides clear diagnostics.

## 9. Step History Tracking

Each job maintains an execution history stored inside its `JobContext`
under the field `step_history`.  
This history records **which step was executed**, **in which phase**, and
**at which pipeline index** during traversal.

### 9.1 Representation

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

### 9.2 Parent and Child Jobs

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

## 10. Summary

The pipeline execution model supports:

- two-phase deterministic traversal,
- asynchronous concurrency via buffers,
- structured parallelism through split and join steps,
- job trees with predictable synchronization,
- robust handling of errors.

This design balances flexibility for pipeline authors with a strong consistency model for parallel execution of quantum jobs.
