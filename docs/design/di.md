# Dependency Injection (DI)

OQTOPUS Engine provides a lightweight dependency injection (DI) mechanism for
instantiating external components such as fetchers, buffers, device interfaces,
and hooks.

This DI system is intentionally minimal, dependency-free (no Hydra/OmegaConf),
and based on plain YAML plus environment variable interpolation.

## 1. Overview

The DI system is built on three concepts:

1. Configuration-based instantiation
2. Environment-variable interpolation
3. Controlled instance lifetime using scopes

## 2. Configuration Syntax

Each DI-enabled component is a dictionary with the following structure.

### Required field

- `_target_`  
  Fully qualified Python class path.  
  Example: `oqtopus_engine_core.fetchers.OqtopusCloudJobFetcher`

### Optional fields

- `_scope_`  
  Defines the lifecycle of the instance.
  - `singleton` (default) — one instance cached and reused
  - `prototype` — new instance created on each `get()` call

### Constructor arguments

All keys **without a leading underscore** are passed to the constructor as
keyword arguments.

Example:

```yaml
di_container:
  registry:
    job_fetcher:
      _target_: oqtopus_engine_core.fetchers.OqtopusCloudJobFetcher
      _scope_: singleton
      url: ${JOB_FETCHER_URL, "http://localhost:8888"}
      interval_seconds: ${JOB_FETCHER_INTERVAL, 10}
    job_repository:
      _target_: oqtopus_engine_core.repositories.NullJobRepository

```

## 3. Environment Variable Interpolation

The configuration loader supports two formats:

### 3.1 `${VAR}` (no default)

- If `VAR` exists → substituted with its value.
- If `VAR` is unset → replaced with an empty string (`""`).

Example:

```yaml
host: ${HOST}
```

### 3.2 `${VAR, default}` (with default)

- If `VAR` exists → substituted with its value.
- If `VAR` is unset → the `default` text is inserted and parsed by PyYAML.

Examples:

```yaml
interval_seconds: ${FETCHER_INTERVAL, 10}
use_ssl: ${USE_SSL, false}
ratio: ${RATIO, 0.75}
settings: ${SETTINGS, {limit: 5, mode: "fast"}}
```

PyYAML performs the final type conversion; the DI system itself does not
implement custom casting logic.

## 4. Instance Creation and Scoping

### 4.1 Class import

The `_target_` string is resolved using `importlib.import_module()`.

Example:

- module: `oqtopus_engine_core.fetchers`
- class:  `OqtopusCloudJobFetcher`

If the module or class does not exist, an `ImportError` is raised.

### 4.2 Constructor arguments

Example:

```yaml
di_container:
  registry:
    device_fetcher:
      _target_: oqtopus_engine_core.fetchers.DeviceGatewayFetcher
      gateway_address: "localhost:52021"
      initial_interval_seconds: 10
```

Constructor call becomes:

```python
DeviceGatewayFetcher(
    gateway_address="localhost:52021",
    initial_interval_seconds=10,
)
```

Keys beginning with `_` (such as `_target_` and `_scope_`) are never passed to
the constructor.

### 4.3 Scopes

#### `singleton` (default)

- The first call to `get(name)` creates an instance.
- The instance is cached inside the container.
- All future calls return the same instance.

#### `prototype`

- Every call to `get(name)` creates a new instance.
- No caching is performed.

Example:

```yaml
di_container:
  registry:
    buffer:
      _target_: oqtopus_engine_core.buffers.QueueBuffer
      _scope_: prototype
      capacity: 1000
```

## 5. Error Handling

The DI container raises clear, predictable exceptions:

- `KeyError`  
  The name does not exist in the configuration.

- `ValueError`  
  The component configuration is missing `_target_`.

- `ImportError`  
  The `_target_` module or class cannot be imported.

- `TypeError`  
  Object construction failed due to argument mismatch.

Errors are not suppressed or silently ignored.

## 6. Full Example

Configuration (`config.yaml`):

```yaml
di_container:
  registry:
    job_fetcher:
      _target_: oqtopus_engine_core.fetchers.OqtopusCloudJobFetcher
      url: ${JOB_FETCHER_URL, "http://localhost:8888"}
      interval_seconds: ${JOB_FETCHER_INTERVAL, 10}
```

Python code:

```python
job_fetcher: JobFetcher = dicon.get("job_fetcher")
```
