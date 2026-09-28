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
`PipelineManager` (which owns one `PipelineExecutor` per pipeline) directly
from the YAML configuration.

The `pipeline_manager` section defines:

- one or more named `pipelines`, each with:
  - an `if` condition (a small boolean expression over `job.xxx` fields)
    used to select which pipeline a job is routed to,
  - the ordered list of pipeline elements for that pipeline;
- which component acts as the job buffer;
- which component handles exceptions during pipeline execution.

Pipelines are evaluated top to bottom; the first one whose `if` evaluates to
`true` is selected. A job that matches no pipeline is failed automatically
via the exception handler (it does not need an explicit catch-all pipeline,
though `if: true` remains valid syntax for one).

A corresponding entry in `di_container.registry` supplies the concrete
implementations for each name.

Example:

```yaml
pipeline_manager:
  job_buffer: buffer
  exception_handler: pipeline_exception_handler
  pipelines:
    - name: sampling
      if: job.job_type == "sampling"
      steps:
        - job_repository_update_step
        - tranqu_step
        - ro_error_mitigation_step
        - buffer
        - device_gateway_step

    - name: sse
      if: job.job_type == "sse"
      steps:
        - job_repository_update_step
        - buffer
        - sse_step

di_container:
  registry:
    job_repository_update_step:
      _target_: oqtopus_engine_core.steps.JobRepositoryUpdateStep

    ...
```

### 2.2 How the Engine Uses This Configuration

The engine reads `pipeline_manager` and retrieves all components from the dependency-injection container:

- for each pipeline, every element (steps and buffers) is retrieved from the DI registry;
- the element order in YAML becomes that pipeline's actual traversal order;
- the job buffer is assigned to `job_buffer`;
- the exception handler is assigned to `exception_handler`.

Internally, the engine uses `PipelineBuilder.build()` to construct the manager:

- validate the config structure (pipeline name uniqueness, non-empty `pipelines`/`steps`);
- compile and type-check each pipeline's `if` condition;
- resolve each pipeline's `steps` via `dicon.get(name)`, building one `PipelineExecutor` per pipeline;
- detect `Buffer` instances referenced by more than one pipeline (by object identity) and route their worker-spawning through the `PipelineManager` instead of the individual executors, so a job dequeued after a shared-buffer hand-off always resumes in its own pipeline;
- retrieve `job_buffer` and `exception_handler`;
- create a `PipelineManager` wrapping all of the above.

Each pipeline's `if` condition is a small boolean expression over `job.xxx`
fields (see `framework/pipeline_condition.py`), parsed and compiled with
[Lark](https://github.com/lark-parser/lark) (a `parser="lalr"` grammar)
exactly once at startup — never re-parsed while processing jobs. Parsing
produces a small frozen-dataclass AST which is field- and type-checked
before the Engine is allowed to start, then evaluated directly (no further
Lark involvement) against each job at pipeline-selection time. Full syntax,
the allowed `job.xxx` fields, and startup validation errors are documented
in [Pipeline Selection Conditions](../usage/pipeline_conditions.md).

At runtime, `PipelineManager.execute_pipeline()` evaluates each pipeline's compiled `if` condition against the incoming job (in YAML order), and delegates to the first matching `PipelineExecutor`.

This allows:

- fully declarative pipeline definition, including job-type-based routing,
- environment-specific override via YAML or environment variables,
- consistent dependency management across all components,
- a clean separation between "pipeline selection," "pipeline construction," and "pipeline execution."

The following sections describe **how a single constructed `PipelineExecutor` executes jobs** including two-phase traversal, buffers, workers, split/join semantics, detach, and error handling. This part of the model is unaffected by pipeline selection: once a job is routed to a pipeline, it runs exactly as described below.

### 2.3 Buffers Shared Across Multiple Pipelines

A `Buffer` instance (e.g. `${JOB_BUFFER, buffer}`) may be referenced by more
than one pipeline's `steps` list. `PipelineBuilder` detects this sharing (by
object identity) and has `PipelineManager` — not the individual
`PipelineExecutor`s — spawn that buffer's worker(s) centrally, dispatching
each dequeued job back to the `PipelineExecutor` for the pipeline it belongs
to (via `pipeline_name`, stored on the job's `JobContext`).

Sharing a `Buffer` object across pipelines is purely an implementation
detail for avoiding duplicate workers and configuration drift; it does
**not** mean the pipeline execution model supports a job crossing from one
pipeline into another. A job's pipeline is fixed for its entire lifetime,
including across any number of buffer hand-offs, splits, or joins — a job
dequeued from a shared buffer must always resume on the `PipelineExecutor`
for the *same* pipeline it started in, never a different one.

This is a hard invariant that buffer/step implementations must uphold
themselves when they build derived jobs and enqueue them onto a shared
buffer. For example, `MpAutoCombiningBuffer` (see
[Configuration](../usage/config.md)) merges several original jobs into one
combined job before re-enqueuing it; it groups jobs by `pipeline_name`
before combining and never merges jobs from different pipelines together,
precisely because a combined job spanning multiple pipelines would have no
single `PipelineExecutor` to resume on.

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

If a step returns `StepResult(directive=PipelineDirective.DETACH)`,
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
pipeline_manager:
  job_buffer: buffer
  pipelines:
    - name: default
      if: true
      steps:
        - buffer          # refers to the entry below

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

A step triggers a split by returning a `StepResult` with one of the split directives
from its `pre_process()` or `post_process()` method:

- **`PipelineDirective.SPLIT_FOR_JOIN`**: children run independently; parent waits until all
  children reach the join step before resuming.
- **`PipelineDirective.SPLIT_WITHOUT_JOIN`**: children run independently; parent does **not**
  wait. A pending-children counter is still registered internally (so cascade
  cleanup can detect when every child has finished), but nothing ever waits
  on it — no join step resumes the parent.

The `StepResult` carries the child jobs and child contexts:

```python
return StepResult(
    directive=PipelineDirective.SPLIT_FOR_JOIN,
    child_jobs=child_jobs,
    child_contexts=child_ctxs,
)
```

When a split occurs:

1. The parent job's traversal **pauses**.
2. The executor calls `link_parent_and_children` to establish `job.children` /
   `jctx.children` — the step must **not** call this function itself. A
   child that already has a `.parent` from an earlier, unrelated split
   (e.g. a job re-emitted by an auto-combining buffer that groups jobs from
   multiple splits together) is left untouched instead: its real `.parent`
   is not overwritten, and it is not counted under this job's
   pending-children counter, since its completion will resolve its real
   parent's counter instead.
3. Each child job starts its own execution:
   - traversal starts from the first pipeline element,
   - workers, buffers, and phases apply independently.
4. For `SPLIT_FOR_JOIN`: the parent job waits until all children complete.

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

A step signals join intent by returning `StepResult(directive=PipelineDirective.JOIN)`
from either its `pre_process()` or `post_process()` method (typically guarded by
`if job.parent is not None`):

- Returning `JOIN` from **`pre_process`**: parent resumes from the join point during
  forward traversal.
- Returning `JOIN` from **`post_process`**: parent resumes during backward traversal.

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

A step triggers a detach by returning
`StepResult(directive=PipelineDirective.DETACH)` from either its
`pre_process()` or `post_process()` method.

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

## 12. Job ID Conventions

Splitting (estimation) and MP auto-combining both create Jobs that have no
entity of their own in the Cloud repository, or that stand in for more than
one. `Job` carries two identifiers to keep this distinction explicit:

- **`job_id`**: the identifier of an execution unit, unique within the
  engine. Every correlation key uses this: logs, OTel baggage, the FIFO
  ordering key (`OqtopusCloudJobRepository._job_tails`), tranqu's
  `request_id`, the combiner's `assigned_ids`. Internally generated jobs
  (estimation children named `{parent}-estimation-{index}`, MP-auto-combined
  jobs named `mpa-comb-{uuid7}`) have a `job_id` that does not exist in the
  Cloud repository.
- **`repository_job_id`**: the record ID in the repository (Cloud). The only
  value that may be placed in an HTTP request path or body. `None` means
  this Job has no repository entity of its own.

`repository_job_id` is a **marker only**: it must never be substituted for
`job_id` in a request path. A "replace" scheme, where a repository
implementation addresses the record by `repository_job_id` directly, would
let N estimation children overwrite the same parent record in turn instead
of being rejected, trading a loud 404 for a silent overwrite.

### 12.1 Four Job/Repository-Entity Relationships

Read `x:y` as **x Job objects (`job_id` space) : y distinct Cloud
repository entities (`repository_job_id` space) they resolve to** — it is
not "child:parent" in a fixed position. Which Job(s) `x` refers to differs
per row: a single Job considered on its own (1:1, 1:0, 1:N), or a group of
sibling children considered together (N:1).

| Relationship | Example |
| --- | --- |
| 1:1 | An ordinary sampling / estimation parent, an SSE-internal job (see §12.3): one Job resolves to one Cloud entity — itself |
| 1:0 | An MP-auto-combined job (`mpa-comb-*`): its own `repository_job_id` is `None`, so considered alone it resolves to no Cloud entity of its own |
| N:1 | An estimation job's sampling children: N sibling Jobs (the children) all resolve to the same one Cloud entity — their shared parent |
| 1:N | A combined job whose children span more than one estimation parent: once its children are resolved, this one Job maps to more than one distinct Cloud entity |

SSE-internal jobs are listed under 1:1, not 1:0: unlike an MP-auto-combined
job, an SSE-internal job is a real, independent execution unit a user
program dispatched, not a stand-in for others (see §12.3). It resolves to
itself, the same as an ordinary top-level job.

1:N arises because `MpAutoCombiningBuffer` groups jobs to combine only by
`pipeline_name` (see §2.3 above), so `P1-estimation-0` and `P2-estimation-1`
can end up in the same combined job.

`resolve_repository_jobs(job)` (in `framework/model.py`) resolves any Job to
the list of repository-tracked Job objects a Cloud update should target:

```python
def resolve_repository_jobs(job: Job) -> list[Job]:
    if job.repository_job_id is None:
        unique: dict[str, Job] = {}
        for child in job.children:
            for resolved in resolve_repository_jobs(child):
                unique[resolved.job_id] = resolved
        return list(unique.values())
    current = job
    while current.job_id != current.repository_job_id and current.parent is not None:
        current = current.parent
    return [current] if current.job_id == current.repository_job_id else []
```

It returns `Job` objects, not `job_id` strings, already deduped by
`job_id` (two children can resolve to the same target, e.g. two estimation
children of the same parent combined together), so callers can mutate the
shared object directly: the `job.status == "ready"` guard in
`DeviceGatewayStep._update_jobs_status` only prevents duplicate PATCHes if
every child resolving to the same parent resolves to that exact same `Job`
instance. An empty result (no repository entity and no children to
delegate to) is expected, not an error, and defensive code should keep
handling it, but in practice no job-creation rule below produces one: an
MP-auto-combined job always has `children` to delegate to, so this path is
a safety net, not something a normal job graph hits.
`OqtopusCloudJobRepository._is_repository_tracked` is the single point that
logs a warning if some other caller bypasses this resolution and reaches
the repository directly with an untracked Job.

### 12.2 Where `repository_job_id` Is Set

The rule is: **a Job created by a fetcher gets `repository_job_id =
job_id`**. Every fetcher follows it:

| Where | Value |
| --- | --- |
| `OqtopusCloudJobRepository.get_jobs` (`Job(**job_oas.to_dict())`) | same as `job_id` (all Cloud-origin jobs go through here) |
| `MockJobFetcher` | same as `job_id` |
| `SseEngineGateway._get_job_from_request` (`Job.model_validate_json`) | same as `job_id`, see §12.3 for why this is safe |

Two exceptions, both for Jobs *not* created by a fetcher — an execution
unit spawned by the engine itself from an already-running job:

| Where | Value |
| --- | --- |
| `EstimatorStep._build_child_job` | the parent's `repository_job_id` |
| `MpAutoCombiningBuffer.create_combined_job` | `None` |

### 12.3 SSE-Internal Job IDs

`sse_runtime/src/sse_runtime/sse_driver.py` gives every internal job that a
container's user program sends (sampling/estimation circuit calls) its own
engine-unique `job_id`, numbered per gRPC call within that container's
process lifetime:

```python
job_id = os.environ.get("JOB_ID")   # the parent SSE job's Cloud job_id
...
request["job_id"] = _next_internal_job_id(job_id)  # f"{job_id}-sse-{index}", 0-origin
request["status"] = "ready"
```

`SseEngineGateway._get_job_from_request` then sets `repository_job_id =
job_id` on the received Job, following the same fetcher-origin rule as
every other Job source (§12.2) — this used to be the one exception, left
`None`, because at the time `job_id` here was the **parent SSE job's own
Cloud `job_id`**, reused unchanged for every internal call. Setting
`repository_job_id = job_id` under that old scheme would have made every
internal job pass the repository entry guard as if it were the parent's
own record and, had a real repository ever been wired in for the SSE
engine, would have clobbered the parent's actual Cloud record
mid-execution: a spurious `running` PATCH racing the outer job's own
transition, a spurious `succeeded` PATCH firing while the user program was
still executing, and the parent's `result`/`transpile_result` overwritten
by an internal child's.

Two changes together close that gap instead of merely leaving
`repository_job_id` unset:

1. **The internal job_id is now engine-unique** (`{parent}-sse-{index}`),
   never equal to the parent SSE job's own `job_id`. `resolve_repository_jobs`
   can no longer mistake an internal job for its own parent's Cloud record.
2. **The SSE engine is a sidecar of the core engine**: only the core engine
   ever holds a real, Cloud-backed `JobRepository`; `sse_engine_config.yaml`
   always wires `NullJobRepository` for the SSE engine's own
   `di_container.registry`. This is an architectural invariant, not a
   coincidence of today's config — the SSE engine is never meant to talk to
   Cloud directly. Every PATCH/upload call `resolve_repository_jobs` routes
   to therefore resolves against `NullJobRepository` and is a no-op,
   regardless of what `repository_job_id` holds.

With both in place, `repository_job_id = job_id` is safe the same way it is
for any other fetcher-origin job, and the engine no longer needs a
special-cased "leave it `None`" exception for SSE. This also means an
SSE-internal `estimation` job's sampling children resolve correctly: a
child's `repository_job_id` (inherited from its parent, per
`EstimatorStep._build_child_job`) now differs from the child's own
`job_id`, and the child's `.parent` is a real, live reference to the exact
`Job` object `SseEngineGatewayServicer.SseEngine` is polling — the same
split/join mechanism as any other estimation job, entirely within one
`SseEngine()` gRPC call. `resolve_repository_jobs` therefore climbs to that
parent correctly, the same as the ordinary N:1 case in §12.1.

## 13. Summary

The pipeline execution model supports:

- two-phase deterministic traversal,
- asynchronous concurrency via buffers,
- structured parallelism through split and join steps,
- job trees with predictable synchronization,
- robust handling of errors.

This design balances flexibility for pipeline authors with a strong consistency model for parallel execution of quantum jobs.
