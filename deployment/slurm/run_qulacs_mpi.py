import argparse
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from mpi4py import MPI
from qulacs import GeneralQuantumOperator, QuantumCircuit, QuantumState, gate


def _add_gate(circuit: QuantumCircuit, operation: dict[str, Any]) -> None:
    name = operation["name"]
    qubits = operation["qubits"]
    params = operation["params"]
    if name == "id":
        circuit.add_gate(gate.Identity(qubits[0]))
    elif name == "sxdg":
        circuit.add_sqrtXdag_gate(qubits[0])
    elif name == "sx":
        circuit.add_sqrtX_gate(qubits[0])
    elif name in {"rx", "ry", "rz"}:
        getattr(circuit, f"add_{name.upper()}_gate")(qubits[0], -params[0])
    elif name in {"p", "u1"}:
        circuit.add_U1_gate(qubits[0], params[0])
    elif name == "u2":
        circuit.add_U2_gate(qubits[0], params[0], params[1])
    elif name in {"u", "u3"}:
        circuit.add_U3_gate(qubits[0], params[0], params[1], params[2])
    elif name == "cx":
        circuit.add_CNOT_gate(qubits[0], qubits[1])
    elif name == "cz":
        circuit.add_CZ_gate(qubits[0], qubits[1])
    elif name == "swap":
        circuit.add_SWAP_gate(qubits[0], qubits[1])
    elif name in {"h", "s", "sdg", "t", "tdg", "x", "y", "z"}:
        method_name = {
            "h": "add_H_gate",
            "s": "add_S_gate",
            "sdg": "add_Sdag_gate",
            "t": "add_T_gate",
            "tdg": "add_Tdag_gate",
            "x": "add_X_gate",
            "y": "add_Y_gate",
            "z": "add_Z_gate",
        }[name]
        getattr(circuit, method_name)(qubits[0])
    else:
        message = f"unsupported worker gate: {name}"
        raise ValueError(message)


def _sample(
    state: QuantumState,
    shots: int,
    measurement_mapping: dict[str, int],
    seed: int | None,
) -> dict[str, int]:
    samples = state.sampling(shots) if seed is None else state.sampling(shots, seed)
    ordered_clbits = sorted((int(index) for index in measurement_mapping), reverse=True)
    counts: Counter[str] = Counter()
    for sample in samples:
        bitstring = "".join(
            str((sample >> measurement_mapping[str(clbit)]) & 1)
            for clbit in ordered_clbits
        )
        counts[bitstring] += 1
    return dict(counts)


def _estimate(
    state: QuantumState,
    n_qubits: int,
    operators: list[dict[str, Any]],
) -> complex:
    operator = GeneralQuantumOperator(n_qubits)
    for term in operators:
        operator.add_operator(term["coeff"], term["pauli"])
    return operator.get_expectation_value(state)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_path, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()

    request = json.loads(args.request.read_text(encoding="utf-8"))
    if request.get("schema_version") != 1:
        message = "unsupported request schema version"
        raise ValueError(message)

    start = time.perf_counter()
    circuit = QuantumCircuit(request["n_qubits"])
    for operation in request["gates"]:
        _add_gate(circuit, operation)

    state = QuantumState(request["n_qubits"], use_multi_cpu=True)
    seed = request.get("seed_simulation")
    if seed is None:
        circuit.update_quantum_state(state)
    else:
        circuit.update_quantum_state(state, seed=seed)

    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "succeeded",
        "job_type": request["job_type"],
    }
    if request["job_type"] == "sampling":
        result["counts"] = _sample(
            state,
            request["shots"],
            request["measurement_mapping"],
            seed,
        )
    elif request["job_type"] == "estimation":
        exp_value = _estimate(state, request["n_qubits"], request["operators"])
        if not math.isfinite(exp_value.real) or not math.isfinite(exp_value.imag):
            message = "non-finite expectation value"
            raise ValueError(message)
        result["exp_value"] = [exp_value.real, exp_value.imag]
    else:
        message = f"unsupported job type: {request['job_type']}"
        raise ValueError(message)

    result["duration_seconds"] = time.perf_counter() - start
    if MPI.COMM_WORLD.Get_rank() == 0:
        _atomic_write(args.result, result)


if __name__ == "__main__":
    main()