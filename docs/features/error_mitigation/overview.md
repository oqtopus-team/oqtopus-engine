# Error Mitigation

OQTOPUS Engine supports error-mitigation methods that transform circuits,
execution results, or both to reduce the effect of device noise. This section
is the entry point for the supported methods and their method-specific designs.

## 1. Supported Methods

| Method | Target | Processing model | Status |
| --- | --- | --- | --- |
| [Local readout-error mitigation](./readout_mitigation/overview.md) | Sampling and estimation | Correct measurement results during post-process | Supported |

Only implemented methods are listed as supported. Design details for a method
belong to that method's pages rather than this overview.

## 2. Method-Specific Processing

Error-mitigation methods do not share one pipeline shape. Depending on the
method, processing may include:

- circuit transformation during pre-process
- multiple device executions through split/join
- result correction or extrapolation during post-process
- method-specific uncertainty calculation

Each method's documentation owns these details:

- applicable job types and configuration
- pipeline placement and control flow
- participating Core steps and services
- gRPC or other service contracts
- intermediate and final result semantics
- validation, limits, and deployment compatibility

This keeps the category open to methods with different execution models without
making the current readout-mitigation flow a contract for future methods.

## 3. Current Feature Documents

- [Readout Error Mitigation](./readout_mitigation/overview.md)
  - [Sampling REM](./readout_mitigation/sampling.md)
  - [Estimation REM](./readout_mitigation/estimation.md)

## 4. Related Features

- [Estimation](../estimation/overview.md)
