from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
import stim  # type: ignore[import-untyped]
from qiskit.exceptions import QiskitError  # type: ignore[import-untyped]
from qiskit.quantum_info import Clifford  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from qiskit import QuantumCircuit  # type: ignore[import-untyped]
    from qiskit.circuit import Operation  # type: ignore[import-untyped]

    from oqtopus_engine_mitigator.qcxt.device import DeviceData

logger = logging.getLogger(__name__)

# Tableau -> name of every 1- and 2-qubit unitary Stim gate, taken from Stim itself.
_STIM_GATE_BY_TABLEAU: dict[str, str] = {
    str(gate.tableau): name
    for name, gate in stim.gate_data().items()
    if gate.is_unitary
    and name == gate.name  # Skip aliases.
    and (gate.is_single_qubit_gate or gate.is_two_qubit_gate)
}
# Z-axis rotations (I, Z, S, S_DAG; e.g. from rz) are virtual gates and get no noise.
_ONE_QUBIT_NOISY_OPS: frozenset[str] = frozenset(
    name
    for name, gate in stim.gate_data().items()
    if gate.is_unitary
    and name == gate.name  # Skip aliases.
    and gate.is_single_qubit_gate
    and name not in {"I", "Z", "S", "S_DAG"}
)
_TWO_QUBIT_NOISY_OPS: frozenset[str] = frozenset({"CX"})
_MEASURE_OPS: frozenset[str] = frozenset({"M", "MR", "MX", "MY", "MZ"})

# Define type aliases for the cache of Stim gate names looked up from Qiskit operations.
type GateKey = tuple[str, tuple[Any, ...]]  # (name, params)
type StimNameCache = dict[GateKey, str | None]  # None if Stim cannot simulate the gate


class Simulator:
    """Stim-based simulator for gate-cutting expectation value estimation.

    Attributes:
        device: Device noise information, or None for ideal simulation.

    """

    def __init__(self, device: DeviceData | None = None) -> None:
        self.device = device
        # (name, params) -> Stim gate name, shared by all circuits of one request.
        self._stim_names: StimNameCache = {}

    def calculate_expval(
        self,
        circuits: list[QuantumCircuit],
        shots: int,
        coefficients: list[float] | None = None,
        *,
        is_noisy_simulated: bool = True,
    ) -> float:
        """Calculate the coefficient-weighted expectation value of a list of circuits.

        Args:
            circuits: Sub-circuits to evaluate.
            shots: Number of measurement shots per sub-circuit.
            coefficients: Expansion coefficients. Defaults to all ones (non-cut case).
            is_noisy_simulated: If True (default) device noise is applied.

        Returns:
            The coefficient-weighted expectation value.

        """
        # Default to all ones if no coefficients are provided (non-cut case).
        if coefficients is None:
            coefficients = [1.0] * len(circuits)

        # Calculate the expectation value for each sub-circuit and sum them up.
        total = 0.0
        for coefficient, circuit in zip(coefficients, circuits, strict=True):
            expval = self._calculate_circuit_expval(
                circuit,
                shots,
                is_noisy_simulated=is_noisy_simulated,
            )
            total += coefficient * expval

        return float(total)

    @staticmethod
    def judge(
        *,
        expval_ideal: float,
        expval_std: float,
        expval_cut: float,
    ) -> bool:
        """Judge whether applying the gate cut reduces the error.

        Args:
            expval_ideal: Ideal (noiseless) expectation value.
            expval_std: Standard (noisy, non-cut) expectation value.
            expval_cut: Gate-cut (noisy) expectation value.

        Returns:
            True if the cut should be applied, False otherwise.

        """
        return abs(expval_ideal - expval_cut) < abs(expval_ideal - expval_std)

    def _calculate_circuit_expval(
        self,
        circuit: QuantumCircuit,
        shots: int,
        *,
        is_noisy_simulated: bool,
    ) -> float:
        """Calculate the expectation value of a single circuit.

        Args:
            circuit: The Qiskit QuantumCircuit to evaluate.
            shots: Number of measurement shots.
            is_noisy_simulated: If True, device noise is applied.

        Returns:
            The estimated expectation value of the single circuit.

        """
        stim_circuit = _to_stim(circuit, self._stim_names)
        device = self.device if is_noisy_simulated else None
        sampling_circuit = (
            _add_noise(stim_circuit, device)
            if device is not None
            else stim_circuit.copy()
        )
        # Add a measurement layer if the circuit has no measurements.
        if sampling_circuit.num_measurements == 0:
            qubits = _active_qubits(sampling_circuit)
            if qubits:
                if device is not None:
                    _append_readout_noise(sampling_circuit, qubits, device)
                sampling_circuit.append("M", qubits)

        return _sample_expectation(sampling_circuit, shots)


def _qubit_targets(instruction: stim.CircuitInstruction) -> list[int]:
    """Extract qubit targets from a Stim circuit instruction.

    Args:
        instruction: The Stim circuit instruction.

    Returns:
        A list of qubit targets for the instruction.

    """
    return [
        target.value for target in instruction.targets_copy() if target.is_qubit_target
    ]


def _active_qubits(circuit: stim.Circuit) -> list[int]:
    """Get a sorted list of active qubits in a Stim circuit.

    Args:
        circuit: The Stim circuit to analyze.

    Returns:
        A sorted list of active qubit indices in the circuit.

    """
    qubits: set[int] = set()
    for instruction in circuit:
        if isinstance(instruction, stim.CircuitInstruction):
            qubits.update(_qubit_targets(instruction))
    return sorted(qubits)


def _append_readout_noise(
    circuit: stim.Circuit,
    qubits: list[int],
    device: DeviceData,
) -> None:
    """Append readout noise to the circuit based on readout error rates."""
    for qubit in qubits:
        error = device.readout_errors[qubit]
        if error > 0.0:
            # Apply readout noise as an X error before measurement
            circuit.append("X_ERROR", [qubit], error)


def _append_operation_with_noise(
    circuit: stim.Circuit,
    name: str,
    targets: list[int],
    device: DeviceData,
) -> None:
    """Append the operation to the circuit with noise based on device's error rates."""
    # Append the measurement operation with readout noise
    if name in _MEASURE_OPS:
        _append_readout_noise(circuit, targets, device)
        circuit.append(name, targets)
        return

    # Append noise for 1-qubit and 2-qubit operations based on device error rates
    circuit.append(name, targets)
    if name in _ONE_QUBIT_NOISY_OPS and len(targets) == 1:
        qubit = targets[0]
        error = device.one_qubit_errors[qubit]
        if error > 0.0:
            circuit.append("DEPOLARIZE1", [qubit], error)
    elif name in _TWO_QUBIT_NOISY_OPS:
        # A 2-qubit Stim instruction lists its targets as (control, target) pairs.
        for control, target in zip(targets[::2], targets[1::2], strict=True):
            error = device.two_qubit_errors[control, target]
            if error > 0.0:
                circuit.append("DEPOLARIZE2", [control, target], error)


def _stim_gate_name(operation: Operation) -> str | None:
    """Find the single Stim gate equal to a Qiskit operation up to global phase.

    Args:
        operation: The Qiskit operation to look up.

    Returns:
        The Stim gate name, or None if Stim cannot simulate the operation.

    """
    # Check Clifford here: Stim alone maps e.g. rz(0.3) to I without an error.
    try:
        clifford = Clifford(operation)
    except QiskitError:
        return None
    tableau = stim.Tableau.from_unitary_matrix(clifford.to_matrix(), endian="little")
    return _STIM_GATE_BY_TABLEAU.get(str(tableau))


def _to_stim(
    circuit: QuantumCircuit,
    stim_names: StimNameCache,
) -> stim.Circuit:
    """Convert a Qiskit QuantumCircuit to a Stim Circuit.

    Instructions that Stim cannot simulate are ignored.

    Args:
        circuit: The Qiskit QuantumCircuit to convert.
        stim_names: Cache of Stim gate names keyed by (name, params); updated in place.

    Returns:
        A Stim Circuit equivalent to the simulable part of the input QuantumCircuit.

    """
    stim_circuit = stim.Circuit()
    for instruction in circuit.data:
        name = instruction.operation.name.lower()
        qubits = [circuit.find_bit(qubit).index for qubit in instruction.qubits]
        if name == "measure":
            stim_circuit.append("M", qubits)
        elif name == "reset":
            stim_circuit.append("R", qubits)
        elif name == "barrier":
            stim_circuit.append("TICK")
        else:
            key: GateKey = (name, tuple(instruction.operation.params))
            if key not in stim_names:
                stim_names[key] = _stim_gate_name(instruction.operation)
            stim_name = stim_names[key]
            if stim_name is None:
                continue  # Not simulable by Stim; ignored.
            stim_circuit.append(stim_name, qubits)
            if stim.gate_data(stim_name).is_two_qubit_gate:
                stim_circuit.append("TICK")  # Separate noise layer after 2Q gate.

    return stim_circuit


def _add_noise(circuit: stim.Circuit, device: DeviceData) -> stim.Circuit:
    """Add noise to a Stim circuit based on the device's error rates.

    Args:
        circuit: The Stim circuit to which noise will be added.
        device: The DeviceData containing error rates.

    Returns:
        A new Stim circuit with noise added according to the device's error rates.

    """
    noisy = stim.Circuit()
    for instruction in circuit:
        if not isinstance(instruction, stim.CircuitInstruction):
            continue

        _append_operation_with_noise(
            noisy,
            instruction.name,
            _qubit_targets(instruction),
            device,
        )

    return noisy


def _sample_expectation(circuit: stim.Circuit, shots: int) -> float:
    """Estimate the expectation value of a Stim circuit by sampling.

    Args:
        circuit: The Stim circuit to sample.
        shots: Number of measurement shots.

    Returns:
        Estimated expectation value based on the parity of measured bits.

    """
    sampler = circuit.compile_sampler()
    samples = sampler.sample(shots=shots)
    parities = np.sum(samples, axis=1) % 2
    eigenvalues = 1 - 2 * parities
    return float(np.mean(eigenvalues))
