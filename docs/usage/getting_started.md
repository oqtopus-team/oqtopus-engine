# Getting started (Work in Progress)

## Prerequisites

Before you start the installation of OQTOPUS Engine, you need to install the following tools:

### Development Environment and Setting Up the Python Environment

See the [Development Environment Setup page](../developer_guidelines/setup.md).


## Configurations

Each service in OQTOPUS Engine uses two configuration files:

- [config.yaml](#configyaml)
- [logging.yaml](#loggingyaml)

!!! info
    You can use environment variables as values in the above YAML files.

### config.yaml

This is the main configuration file used by each service in OQTOPUS Engine.
The content of this file varies depending on the service.

### logging.yaml

This is the logging configuration file used by each service in OQTOPUS Engine.
It is written in YAML format.
Within OQTOPUS Engine, it is loaded as a `dict`, and then the [logging.config.dictConfig function](https://docs.python.org/3/library/logging.config.html#logging.config.dictConfig) is called to apply the configuration.

If you use the default settings of `config.yaml`, the `logs` directory is required.

```shell
mkdir logs
```

## Run OQTOPUS Engine Services

### core

Move into the `core` directory and run using `uv`:

```shell
uv run python src/oqtopus_engine_core/app.py -c config/config.yaml -l config/logging.yaml
```

- `-c` or `--config`: Specifies the path to the main configuration file.
- `-l` or `--logging`: Specifies the path to the logging configuration file.

Or run using `make`:

```shell
make run
```

### sse engine

Move into the `core` directory and run using `uv`:

```shell
uv run python src/oqtopus_engine_core/app.py -c config/sse_engine_config.yaml -l config/sse_engine_logging.yaml
```

The command-line options (`-c` / `--config` and `-l` / `--logging`) work the same way as in the [core section](#core).

Or run using `make`:

```shell
make run-sse
```

### mitigator

Move into the `mitigator` directory and run using `uv`:

```shell
uv run python src/oqtopus_engine_mitigator/app.py -c config/config.yaml -l config/logging.yaml
```

The command-line options (`-c` / `--config` and `-l` / `--logging`) work the same way as in the [core section](#core).

Or run using `make`:

```shell
make run
```

### estimator

Move into the `estimator` directory and run using `uv`:

```shell
uv run python src/oqtopus_engine_estimator/app.py -c config/config.yaml -l config/logging.yaml
```

The command-line options (`-c` / `--config` and `-l` / `--logging`) work the same way as in the [core section](#core).

Or run using `make`:

```shell
make run
```

### combiner

Move into the `combiner` directory and run using `uv`:

```shell
uv run python src/oqtopus_engine_combiner/app.py -c config/config.yaml -l config/logging.yaml
```

The command-line options (`-c` / `--config` and `-l` / `--logging`) work the same way as in the [core section](#core).

Or run using `make`:

```shell
make run
```

## Other Operations

For additional operations such as linting, or testing, please refer to the **Makefile** provided in each service's directory.

Each service (e.g., `core`, `mitigator`, `estimator`, `combiner`) includes its own Makefile, which defines service-specific commands and workflows.
