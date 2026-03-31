# Implementing Repository

Repositories provide the interface between the OQTOPUS Engine and
external systems. A repository is responsible for retrieving data from
external services and sending updates back to those services.

Typical repository implementations communicate with external systems
through HTTP APIs or other service interfaces.

This section describes how to implement repositories used by the engine.

## Job Repository

`JobRepository` is responsible for retrieving jobs and updating
job-related information in an external system.

Typical implementations communicate with a remote service (for example
Oqtopus Cloud) through HTTP APIs.

### Responsibilities

A `JobRepository` implementation is responsible for:

- retrieving jobs from an external service
- converting them into `Job` objects used by the engine
- sending job updates back to the external service

Its responsibility is strictly data transport between the engine and
the external system.

### Typical Methods

Repository implementations usually provide the following methods.

#### Fetching jobs

```python
async def get_jobs(...)
```

Fetch jobs from the external system and return a list of `Job` objects.
This method is typically called by the scheduler to obtain new work.

#### Updating job status

```python
async def update_job_status(...)
```

Send a request to update the job status.
This method waits for the response.

#### Updating job information

```python
async def update_job_info(...)
```

Update job info.
This method waits for the response.

#### Updating transpiler information

```python
async def update_job_transpiler_info(...)
```

Update transpiler info.
This method waits for the response.

#### Uploading sse logs

```python
async def update_sselog(...)
```

Upload sse logs.
This method waits for the response.

### `_nowait` Methods

Many repository implementations also provide methods ending with
`_nowait`.

Examples:

```python
update_job_status_nowait(...)
update_job_info_nowait(...)
update_job_transpiler_info_nowait(...)
update_sselog_nowait(...)
```

These methods schedule background requests and **return immediately
without waiting for the HTTP response**.

This allows the engine to continue processing without blocking on
network I/O.

#### Ordering Requirements for `_nowait` Methods

Although `_nowait` methods return immediately, implementations **must
guarantee ordering for operations affecting the same `job_id`**.

If the following calls occur:

```python
update_job_status_nowait(...)
update_job_info_nowait(...)
update_job_transpiler_info_nowait(...)
update_sselog_nowait(...)
```

the repository must ensure that the underlying operations are executed
sequentially and cannot overtake each other.

In other words:

```text
transpiler info update
↓
status update
↓
job info update
```

must always occur in this order for the same `job_id`.

This requirement prevents inconsistent job states caused by out-of-order
updates.

#### Implementation Strategy

A common strategy is to serialize operations per `job_id`.

Typical approaches include:

- per-job task chains
- per-job queues
- per-job locks

The reference implementation (`OqtopusCloudJobRepository`) uses a
per-job task chain to enforce ordering.

Conceptually:

```text
job_id
↓
previous task
↓
next task waits previous
```

This guarantees that operations targeting the same job are never
executed concurrently.

### Concurrency Considerations

`OqtopusCloudJobRepository` performs HTTP requests to Oqtopus Cloud.
The maximum number of concurrent requests is controlled by the
`workers` parameter in the constructor.

Internally, the repository uses an `asyncio.Semaphore` to enforce
this limit.

Each HTTP request acquires the semaphore before performing network I/O.
This ensures that the number of simultaneous requests to Oqtopus Cloud
does not exceed the configured `workers` value.

### Reference Implementation

See `OqtopusCloudJobRepository` for a production-ready example of a
`JobRepository` implementation.

## Device Repository

`DeviceRepository` is responsible for updating device information
stored in an external system that manages devices visible to users.

Examples of such systems include OQTOPUS Cloud.

The OQTOPUS Engine itself does not own device metadata. Instead,
device information maintained by the engine is synchronized with
the external system through the `DeviceRepository`.

Typically, device status and topology are obtained from
Device Gateway by the `JobFetcher`. The engine periodically
retrieves this information and updates the external system
through the repository.

Typical responsibilities include:

- updating device status obtained from Device Gateway
- updating device topology information
- synchronizing device metadata with the external system

In other words, `DeviceRepository` acts as a bridge that
propagates device state observed by the engine to the external
device management system.
