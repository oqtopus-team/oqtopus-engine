from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from qiskit import QuantumCircuit  # type: ignore[import-untyped]


@dataclass(frozen=True)
class DeviceData:
    """Device values used by one circuit in the MIP solver and simulator.

    Holds only the qubits and CX pairs that the circuit uses.

    Attributes:
        one_qubit_fidelities: One-qubit gate fidelities.
        two_qubit_fidelities: Two-qubit gate fidelities.
        readout_errors: Measurement error rates.

    """

    one_qubit_fidelities: dict[int, float]
    two_qubit_fidelities: dict[tuple[int, int], float]
    readout_errors: dict[int, float]

    @property
    def one_qubit_errors(self) -> dict[int, float]:
        """One-qubit error rates derived from one_qubit_fidelities."""
        return {
            qid: (1.0 - fidelity) for qid, fidelity in self.one_qubit_fidelities.items()
        }

    @property
    def two_qubit_errors(self) -> dict[tuple[int, int], float]:
        """Two-qubit error rates derived from two_qubit_fidelities."""
        return {
            pair: (1.0 - fidelity)
            for pair, fidelity in self.two_qubit_fidelities.items()
        }

    @classmethod
    def from_dict(
        cls, device_info: dict[str, Any], circuit: QuantumCircuit
    ) -> Self | None:
        """Extract the device values that the circuit uses.

        Args:
            device_info: A dictionary containing device parameters.
            circuit: The circuit whose qubits and CX pairs are looked up.

        Returns:
            DeviceData holding only the values the circuit uses, or None if any of
            them is missing, not a number, or outside [0, 1].

        """
        # Barriers are skipped because no device value is read for them.
        qubits = {
            circuit.find_bit(qubit).index
            for instruction in circuit.data
            if instruction.operation.name != "barrier"
            for qubit in instruction.qubits
        }
        cx_pairs = {
            (
                circuit.find_bit(instruction.qubits[0]).index,
                circuit.find_bit(instruction.qubits[1]).index,
            )
            for instruction in circuit.data
            if instruction.operation.name == "cx"
        }

        try:
            qubit_info = {qubit.get("id"): qubit for qubit in device_info["qubits"]}
            coupling_info = {
                (coupling.get("control"), coupling.get("target")): coupling
                for coupling in device_info["couplings"]
            }
            one_qubit_fidelities = {
                qid: float(qubit_info[qid]["fidelity"]) for qid in qubits
            }
            readout_errors = {
                qid: float(qubit_info[qid]["meas_error"]["readout_assignment_error"])
                for qid in qubits
            }
            two_qubit_fidelities = {
                pair: float(coupling_info[pair]["fidelity"]) for pair in cx_pairs
            }
        except (KeyError, TypeError, ValueError):
            # Absent key or entry, null value, or a value that is not a number.
            return None

        # NaN fails this comparison too, so non-finite values are rejected.
        values = [
            *one_qubit_fidelities.values(),
            *readout_errors.values(),
            *two_qubit_fidelities.values(),
        ]
        if not all(0.0 <= value <= 1.0 for value in values):
            return None

        return cls(
            one_qubit_fidelities=one_qubit_fidelities,
            two_qubit_fidelities=two_qubit_fidelities,
            readout_errors=readout_errors,
        )
