from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class DeviceData:
    """Class to hold device information to be used in the MIP solver and simulator.

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
    def from_dict(cls, device_info: dict[str, Any]) -> Self:
        """Create a DeviceData instance from a dictionary containing device information.

        Args:
            device_info: A dictionary containing device parameters.

        Returns:
            An instance of DeviceData with extracted device information.

        Raises:
            ValueError: If a required key is missing, a value has a wrong type,
                a qubit ID is duplicated, or a fidelity or readout error is
                outside [0, 1].

        """
        try:
            # Extract (id, fidelity, readout error) of each qubit
            qubits = [
                (
                    int(qubit["id"]),
                    float(qubit["fidelity"]),
                    float(qubit["meas_error"]["readout_assignment_error"]),
                )
                for qubit in device_info["qubits"]
            ]
            # Extract two-qubit fidelities
            two_qubit_fidelities = {
                (int(coupling["control"]), int(coupling["target"])): float(
                    coupling["fidelity"]
                )
                for coupling in device_info["couplings"]
            }
        except (KeyError, TypeError) as e:
            msg = f"Invalid device_info: {e!r}"
            raise ValueError(msg) from e

        id_counts = Counter(qid for qid, _, _ in qubits)
        duplicates = sorted(qid for qid, count in id_counts.items() if count > 1)
        if duplicates:
            msg = f"Duplicate qubit ids in device_info: {duplicates}"
            raise ValueError(msg)
        one_qubit_fidelities = {qid: fidelity for qid, fidelity, _ in qubits}
        readout_errors = {qid: error for qid, _, error in qubits}

        # NaN fails these comparisons too, so non-finite values are rejected.
        for label, values in (
            ("qubit fidelity", one_qubit_fidelities),
            ("readout_assignment_error", readout_errors),
            ("coupling fidelity", two_qubit_fidelities),
        ):
            out_of_range = {k: v for k, v in values.items() if not 0.0 <= v <= 1.0}
            if out_of_range:
                msg = f"{label} must be in [0, 1]: {out_of_range}"
                raise ValueError(msg)

        return cls(
            one_qubit_fidelities=one_qubit_fidelities,
            two_qubit_fidelities=two_qubit_fidelities,
            readout_errors=readout_errors,
        )
