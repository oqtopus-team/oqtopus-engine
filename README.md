![OQTOPUS logo](./docs/asset/oqtopus_logo.png)

# OQTOPUS Engine

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![slack](https://img.shields.io/badge/slack-OQTOPUS-pink.svg?logo=slack&style=plastic")](https://oqtopus.slack.com/archives/C08JXREG0LD)

## Overview

OQTOPUS Engine is an execution engine for optimizing quantum computations at the quantum circuit 
layer. It enhances execution efficiency by performing circuit-level optimizations and 
handles preprocessing and postprocessing of computation results. 
With high concurrency and scalability, it is a powerful tool for quantum computing 
applications.  
The OQTOPUS Engine consists of multiple components centered around the core app, 
with each component compiled within its respective directory and forming a microservices architecture
through protocols such as gRPC. Installation and startup instructions for each component can be found 
in the respective directories.

## Prerequisites

### Task Runner Installation

This project utilizes various commands using `Task`.
To ensure smooth execution, please install `Task` in your environment before proceeding.
For installation instructions, please refer to the [official Task documentation](https://taskfile.dev/installation/).

Once installed, you can verify the installation by running:
```sh
task --version
```

## Components

### Engine

See the `coreapp` README(`coreapp/README.md`) for details on the engine.

## Citation

You can use the DOI to cite OQTOPUS Engine in your research.

[![DOI](https://zenodo.org/badge/947786558.svg)](https://zenodo.org/badge/latestdoi/947786558)

Citation information is also available in the [CITATION](https://github.com/oqtopus-team/oqtopus-engine/blob/main/CITATION.cff) file.

## Contact

You can contact us by creating an issue in this repository or by email:

- [oqtopus-team[at]googlegroups.com](mailto:oqtopus-team[at]googlegroups.com)

## License

OQTOPUS Engine is released under the [Apache License 2.0](https://github.com/oqtopus-team/oqtopus-engine/blob/main/LICENSE).
