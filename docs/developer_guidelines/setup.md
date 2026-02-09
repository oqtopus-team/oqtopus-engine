
# Development Environment Setup

## Prerequisites

Before starting development, you need to install the following tools:

### Development Environment

| Tool                                            | Version  | Description                        |
|-------------------------------------------------|----------|------------------------------------|
| [Python](https://www.python.org/downloads/)     | >=3.13   | Python programming language        |
| [uv](https://docs.astral.sh/uv/)                | >=0.9.16 | Python package and project manager |
| [Buf CLI](https://buf.build/docs/installation/) | -        | developer tool that enables building and management of Protobuf APIs through the command line |
| [Java](https://openjdk.org/)                    | >=21.0.0 | To generate Python code from the OpenAPI Specification, you need Java |

To start development, clone the repository:

```shell
git clone https://github.com/oqtopus-team/oqtopus-engine.git
cd oqtopus-engine
```

### Setting Up the Python Environment

Each backend component maintains its own Python environment.
To install dependencies for a specific component, move into its directory and run:

- core / sse engine (both are managed in the same directory)

```shell
cd core
uv sync
```

- mitigator

```shell
cd mitigator
uv sync
```

- estimator

```shell
cd estimator
uv sync
```

- combiner

```shell
cd combiner
uv sync
```

- tranqu

The transpiler service is provided by the Tranqu Server.
Please refer to the [Tranqu Server documentation](https://tranqu-server.readthedocs.io/) for how to set up and run this component.

- gateway

The Device Gateway connects OQTOPUS Engine to the underlying pulse-control system.
Please refer to the [Device Gateway documentation](https://device-gateway.readthedocs.io/) for environment setup and usage.

## Generate Python code from *.proto file

If you modify a `*.proto file`, run the following commands to generate the gRPC-related code.

- estimation_interface

```shell
cd spec/estimation_interface
make generate
```

- mitigation_interface

```shell
cd spec/mitigation_interface
make generate
```

- multiprog_interface

```shell
cd spec/multiprog_interface
make generate
```

- qpu_interface

```shell
cd spec/qpu_interface
make generate
```

- sse_interface

```shell
cd spec/sse_interface
make generate
```

- tranqu_server

```shell
cd spec/tranqu_server
make generate
```

## Generate Python code from Open API Specification (OAS) file (*.yaml)

To generate Python code for the OQTOPUS Engine from Open API Specification (OAS) files, run the following commands:

- OQTOPOUS Cloud Interface

```shell
cd spec/oqtopus-cloud
make download-oas
make generate
```

## Lint and test

Run the following commands in the directory of the service you want to lint or test.

### How to Format Code

To format the code, run the following command:

```shell
uv run ruff format
```

### How to Lint Code

To check the types, run the following command:

```shell
uv run ruff check
```

### How to Check Types

To check the types, run the following command:

```shell
uv run mypy
```

### How to Test Code

To test the code, run the following command:

```shell
uv run pytest
```
