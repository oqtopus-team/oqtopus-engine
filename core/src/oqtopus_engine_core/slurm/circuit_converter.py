import hashlib
import json
import math
from typing import Literal

# ruff: noqa: DOC201, DOC501
from qiskit import qasm3  # type: ignore[import-untyped]

from oqtopus_engine_core.framework import Job

from .models import (
    OperatorTerm,
    QulacsExecutionRequest,
    QulacsGate,
    SlurmSimulatorOptions,
)
from .operator_mapping import map_operator_items, normalize_qubit_mapping

_SUPPORTED_GATES: dict[str, tuple[int, int]] = {
    "cx": (2, 0),
    "cz": (2, 0),
    "h": (1, 0),
    "id": (1, 0),
    "p": (1, 1),
    "rx": (1, 1),
    "ry": (1, 1),
    "rz": (1, 1),
    "s": (1, 0),
    "sdg": (1, 0),
    "swap": (2, 0),
    "sx": (1, 0),
    "sxdg": (1, 0),
    "t": (1, 0),
    "tdg": (1, 0),
    "u": (1, 3),
    "u1": (1, 1),
    "u2": (1, 2),
    "u3": (1, 3),
    "x": (1, 0),
    "y": (1, 0),
    "z": (1, 0),
}


def convert_transpiled_qasm(  # noqa: C901
    program: str,
    *,
    job_type: Literal["sampling", "estimation"],
) -> tuple[int, list[QulacsGate], dict[int, int]]:
    """Convert allowlisted static OpenQASM 3 into a data-only worker IR."""
    circuit = qasm3.loads(program)
    gates: list[QulacsGate] = []
    measurement_mapping: dict[int, int] = {}
    measurement_started = False

    for instruction in circuit.data:
        name = instruction.operation.name
        if name == "barrier":
            continue
        qubits = [circuit.find_bit(qubit).index for qubit in instruction.qubits]
        clbits = [circuit.find_bit(clbit).index for clbit in instruction.clbits]

        if name == "measure":
            if job_type == "estimation":
                message = "direct estimation does not accept measurement operations"
                raise ValueError(message)
            measurement_started = True
            if clbits[0] in measurement_mapping:
                message = "each classical bit must be measured exactly once"
                raise ValueError(message)
            measurement_mapping[clbits[0]] = qubits[0]
            continue
        if measurement_started:
            message = "only terminal measurements are supported"
            raise ValueError(message)
        if name not in _SUPPORTED_GATES:
            message = f"unsupported Qulacs operation: {name}"
            raise ValueError(message)

        expected_qubits, expected_params = _SUPPORTED_GATES[name]
        params = [float(param) for param in instruction.operation.params]
        if len(qubits) != expected_qubits or len(params) != expected_params:
            message = f"unexpected operands for Qulacs operation: {name}"
            raise ValueError(message)
        if not all(math.isfinite(param) for param in params):
            message = f"non-finite parameter for Qulacs operation: {name}"
            raise ValueError(message)
        control_state = getattr(instruction.operation, "ctrl_state", None)
        if name in {"cx", "cz"} and control_state == 0:
            message = f"negated control is unsupported: {name}"
            raise ValueError(message)
        gates.append(QulacsGate(name=name, qubits=qubits, params=params))

    if job_type == "sampling" and not measurement_mapping:
        message = "sampling requires at least one terminal measurement"
        raise ValueError(message)
    if job_type == "sampling" and set(measurement_mapping) != set(
        range(circuit.num_clbits)
    ):
        message = "sampling requires every classical bit to be measured"
        raise ValueError(message)
    return circuit.num_qubits, gates, measurement_mapping


def build_execution_request(
    job: Job,
    options: SlurmSimulatorOptions,
) -> QulacsExecutionRequest:
    """Build the canonical filesystem request for one Engine job."""
    if job.job_type not in {"sampling", "estimation"}:
        message = f"unsupported SLURM simulator job type: {job.job_type}"
        raise ValueError(message)
    if job.transpile_result is None:
        message = "transpile_result is required for SLURM simulation"
        raise ValueError(message)

    job_type: Literal["sampling", "estimation"] = job.job_type  # type: ignore[assignment]
    n_qubits, gates, measurement_mapping = convert_transpiled_qasm(
        job.transpile_result.transpiled_program,
        job_type=job_type,
    )
    operators: list[OperatorTerm] = []
    if job_type == "estimation":
        if not job.operator:
            message = "operator is required for direct estimation"
            raise ValueError(message)
        operators = map_operator_items(
            job.operator,
            normalize_qubit_mapping(
                job.transpile_result.virtual_physical_mapping,
            ),
            n_qubits=n_qubits,
        )

    return QulacsExecutionRequest(
        job_type=job_type,
        n_qubits=n_qubits,
        gates=gates,
        measurement_mapping=measurement_mapping,
        shots=job.shots if job_type == "sampling" else None,
        operators=operators,
        seed_simulation=options.seed_simulation,
        n_per_node=options.n_per_node,
    )


def canonical_request_json(request: QulacsExecutionRequest) -> str:
    """Serialize a request deterministically for hashing and file transfer."""
    return request.model_dump_json(exclude_none=True, by_alias=True)


def request_hash(
    request: QulacsExecutionRequest,
    options: SlurmSimulatorOptions | None = None,
) -> str:
    """Return a SHA-256 digest of worker input and resolved SLURM resources."""
    if options is None:
        payload = canonical_request_json(request)
    else:
        payload = json.dumps(
            {
                "options": options.model_dump(exclude_none=True, mode="json"),
                "request": request.model_dump(exclude_none=True, mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    return hashlib.sha256(payload.encode()).hexdigest()
