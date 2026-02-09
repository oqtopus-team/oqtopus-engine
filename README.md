![OQTOPUS logo](./docs/asset/oqtopus-logo.png)

# OQTOPUS Engine

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![slack](https://img.shields.io/badge/slack-OQTOPUS-pink.svg?logo=slack&style=plastic")](https://oqtopus.slack.com/archives/C08JXREG0LD)

## Overview

**OQTOPUS Engine** in the backend layer retrieves jobs managed in OQTOPUS Cloud and executes quantum programs, working with **Tranqu Server**, providing the transpilers and **Device Gateway**, which serves as an interface for connecting to a pulse controller.
OQTOPUS Engine features **Server-Side Execution (SSE)**, **Multi-Programming**, **Error Mitigation**, **Estimation**, a **Transpiler** (utilizing Tranqu), and more.
OQTOPUS Engine is organized as a microservice architecture centered around the core process, which provides a job-processing framework (the pipeline).

## Documentation

- [Documentation Home](./docs/index.md)

## Citation

You can use the DOI to cite OQTOPUS Engine in your research.

[![DOI](https://zenodo.org/badge/947786558.svg)](https://zenodo.org/badge/latestdoi/947786558)

Citation information is also available in the [CITATION](https://github.com/oqtopus-team/oqtopus-engine/blob/main/CITATION.cff) file.

## Contact

You can contact us by creating an issue in this repository or by email:

- [oqtopus-team[at]googlegroups.com](mailto:oqtopus-team[at]googlegroups.com)

## License

OQTOPUS Engine is released under the [Apache License 2.0](https://github.com/oqtopus-team/oqtopus-engine/blob/main/LICENSE).
