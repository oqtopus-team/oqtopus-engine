# Sampling Readout Mitigation

This document describes the local readout-mitigation path for sampling results.
The path converts raw measurement counts into mitigated integer counts, stores
them in the sampling result, and preserves the raw counts and intermediate
quasi-probabilities under `mitigation_details`. It covers pipeline
responsibilities, the correction algorithm, external-library usage, and the
gRPC contract.

For the shared assignment-error model and measurement mapping, see
[Readout Error Mitigation](./overview.md).

## 1. Design Goals

A sampling job must return integer counts through its existing result contract.
The REM path therefore:

- corrects the complete measured probability distribution
- projects potentially negative quasi-probabilities onto a valid probability
    distribution
- converts the projected probabilities back to integer counts
- exposes the observed counts and pre-projection quasi-probabilities
- keeps Pauli operators and expectation-value semantics out of the sampling RPC

The probability-to-count conversion can discard fractional counts. Estimation
uses a separate direct expectation-value path to avoid that loss of precision.

## 2. Service Responsibilities

| Component | Responsibility |
| --- | --- |
| Device Gateway and QPU | Execute the sampling circuit and return raw counts. |
| Core `ReadoutErrorMitigationStep` | Select the counts path, build the request from device calibration data, replace the sampling result counts, and store available intermediate details. |
| Mitigator service | Reconstruct the measurement layout, configure local REM, and return the quasi-probabilities plus projected integer counts. |

## 3. Processing Sequence

```mermaid
sequenceDiagram
    autonumber
    participant Core as Core Pipeline
    participant Gateway as Device Gateway / QPU
    participant Mitigator as Mitigator Service

    Core->>Gateway: Execute sampling QASM
    Gateway-->>Core: Raw counts
    Note over Core: Post-process reaches ReadoutErrorMitigationStep

    alt mitigation_info.ro_error_mitigation is pseudo_inverse
        Core->>Mitigator: ReqMitigation(topology, counts, QASM)
        Mitigator->>Mitigator: Reconstruct measurement layout
        Mitigator->>Mitigator: Build local assignment matrices
        Mitigator->>Mitigator: Compute and project quasi-probabilities
        Mitigator-->>Core: Counts, quasi-probabilities, details capability
        Note over Core: Replace counts and store available details
    else REM is not configured
        Note over Core: Preserve raw counts
    end
```

## 4. Detailed Flow

### 4.1 Core Routing

After device execution, `ReadoutErrorMitigationStep` performs the following
operations:

1. It checks `mitigation_info.ro_error_mitigation`.
2. It reads `prob_meas1_prep0` and `prob_meas0_prep1` for each device qubit and
   maps them to `p0m1` and `p1m0` in the Mitigator request.
3. It sends the device topology, raw counts, and executed QASM program through
   `ReqMitigation`.
4. It replaces `job.result.sampling.counts` with the returned counts.
5. When `mitigation_details_available` is true, it stores the original counts
    and returned quasi-probabilities under
    `mitigation_details.ro_error_mitigation`.

The counts path is selected when the `JobContext` does not contain estimation
Pauli metadata.

### 4.2 Distribution Correction

The Mitigator service:

1. Parses the QASM program and orders measured qubits by classical-bit index.
2. Creates a `LocalReadoutMitigator` from the selected qubits' assignment
   matrices.
3. Calls `quasi_probabilities()` with the observed counts and selected layout.
4. Projects the quasi-distribution to its nearest probability distribution.
5. Converts each probability to an integer count using the original shot count.

### 4.3 Result Replacement and Multi-Program Ordering

Core replaces `job.result.sampling.counts` with the returned counts. For a
normal sampling job, this is the final sampling result.

For a `multi_manual` job, the counts represent the combined sampling result.
Post-process traverses pipeline steps in reverse order, so REM corrects the
combined counts before `MultiManualStep` separates them into per-program
results.

For a normal sampling job, the relevant result shape is:

```json
{
    "sampling": {
        "counts": {"00": 524, "11": 474, "01": 0}
    },
    "mitigation_details": {
        "ro_error_mitigation": {
            "method": "local_readout_mitigation",
            "raw_counts": {"00": 475, "11": 430, "01": 48, "10": 47},
            "quasi_probabilities": {
                "00": 0.5250000009,
                "11": 0.4750000008,
                "01": 0.0005555547,
                "10": -0.0005555564
            },
            "expectation_values": null
        }
    }
}
```

## 5. External Library Use

| Operation | Owner |
| --- | --- |
| Parse the executed OpenQASM 3 program | Qiskit `qasm3.loads()`, backed by `qiskit-qasm3-import` |
| Represent observed counts | Qiskit `Counts` |
| Invert local assignment matrices and compute mitigated quasi-probabilities | Qiskit Experiments `LocalReadoutMitigator.quasi_probabilities()` |
| Project quasi-probabilities to the nearest probability distribution | Qiskit `QuasiDistribution.nearest_probability_distribution()` |
| Select measured bits, construct assignment matrices, and convert probabilities to integer counts | OQTOPUS Mitigator service |

The mitigation algorithm is therefore not implemented entirely by OQTOPUS.
OQTOPUS adapts device and circuit data to the external APIs and owns the final
sampling-result conversion. External dependency declarations are linked from the
[common REM overview](./overview.md#6-implementation-ownership-and-external-dependencies).

## 6. gRPC Contract

| Message | Fields used |
| --- | --- |
| `ReqMitigationRequest` | `device_topology`, `counts`, `program` |
| `ReqMitigationResponse` | `counts`, `quasi_probabilities`, `mitigation_details_available` |

Sampling REM does not accept Pauli labels and does not return expectation
values. Those semantics belong to the separate estimation REM contract.

## 7. Validation and Limits

- The OpenQASM 3 program must parse successfully and contain measurement
    operations.
- All measured destinations are included in the correction, up to the shared
    limit of 32 measured qubits.
- The output remains integer-valued. Truncating each corrected probability
    multiplied by the shot count can discard fractional counts.
- Sampling REM does not validate or process Pauli labels.
- A legacy Mitigator response has `mitigation_details_available=false`. Core
    still uses its mitigated counts but omits `mitigation_details`.

## 8. Deployment Compatibility

Deploy Mitigator before Core so new jobs expose intermediate details
immediately. During a rolling upgrade, a new Core accepts responses from an old
Mitigator, preserves the existing mitigated-count behavior, and omits details
that the old server cannot provide. For rollback, roll back Core before
Mitigator.
