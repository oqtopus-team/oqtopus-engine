# Overview

This document provides an overview of the entire quantum computer platform implemented with OQTOPUS, as well as OQTOPUS Engine, the central component of the backend layer.
It is intended for developers and operators of quantum computer platforms.

## Overview of Quantum Computer Platform

Because quantum computers are extremely expensive, they are typically shared among multiple users and accessed as a cloud service.
The architecture of OQTOPUS consists of three layers:

- **Frontend Layer**: Users write quantum programs. The quantum programs to be executed are submitted as jobs to the cloud layer.
- **Cloud Layer**: This layer manages master data such as jobs and users. It is implemented as a web system.
While it is intended to run on public clouds such as AWS, it is also designed to operate as much as possible on a local PC.
- **Backend Layer**: Operates inside the laboratory. It transpiles and schedules jobs and executes them on the QPU.

![OQTOPUS Architecture](../asset/oqtopus-architecture.png)

## Components (Services) of OQTOPUS Engine

The backend layer consists of the following services.
Except for Tranqu and the Gateway, all components listed below are part of OQTOPUS Engine.

- **core**: The central process of OQTOPUS Engine. It contains the job-processing pipeline, communicates with other backend services, and executes jobs retrieved from the cloud layer.
- **sse engine**: A lightweight execution engine for Server-Side Execution (SSE). It is a reduced version of the core process, implementing a subset of Engine functionality required to run SSE workloads.
This component enables low-latency execution and simplified orchestration for SSE tasks.
- **mitigator**: Provides error-mitigation functionality. It applies necessary circuit modifications for mitigation and computes mitigated results after execution.
- **estimator**: A service responsible for expectation-value estimation. It transforms quantum circuits for XYZ-basis measurements and computes expectation values from measurement results.
- **combiner**: Implements manual Multi-Programming support. It combines multiple quantum circuits into a single circuit and later splits aggregated measurement results.
- **tranqu**: The transpiler service, implemented by the Tranqu Server. It provides transpilation, translation, and circuit rewriting functionality.
- **gateway**: The Device Gateway, which links OQTOPUS Engine to the underlying pulse-control system. It acts as the interface layer for communicating with hardware controllers.

All backend processes communicate via gRPC. Interface definitions are maintained in the [spec](../../spec) directory.

## Concepts Behind OQTOPUS Engine

### Use of a Framework

Quantum computing technology is evolving rapidly, and the functions required of a quantum computer system constantly change as well.
OQTOPUS Engine plays a central role in the backend layer of a quantum computing system and must allow functions to be developed and extended flexibly and quickly.

To achieve this, the project adopts an architecture that emphasizes extensibility:
a framework is constructed first, and various OQTOPUS Engine applications are implemented on top of it.
This enables seamless adaptation to future changes, such as updates to quantum algorithms or support for new hardware.

Many quantum-computing workflows involve classical computation before and after QPU execution—for example:

- preprocessing such as circuit optimization or resource allocation
- postprocessing such as analyzing measurement results

The OQTOPUS Engine framework is designed to handle these processes consistently, making it easier to describe and maintain complex workflows.

### Development in Python

OQTOPUS Engine uses Python as its main development language.
In the quantum-computing field, major libraries are provided in Python, and leveraging this rich ecosystem enables rapid development and a maintainable codebase.

However, if performance bottlenecks arise in the future, certain components may be reimplemented in other languages for higher execution speed.
