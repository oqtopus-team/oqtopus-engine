import pytest
from qiskit import QuantumCircuit  # type: ignore[import-untyped]

from oqtopus_engine_mitigator.qcxt.device import DeviceData


def make_device_info() -> dict:
    return {
        "qubits": [
            {
                "id": 0,
                "fidelity": 0.999,
                "meas_error": {"readout_assignment_error": 0.01},
            },
            {
                "id": 1,
                "fidelity": 0.998,
                "meas_error": {"readout_assignment_error": 0.02},
            },
            {"fidelity": None},  # Unused: no ID and no values.
        ],
        "couplings": [
            {"control": 0, "target": 1, "fidelity": 0.98},
            {"fidelity": 0.97},  # Unused: no control or target.
        ],
    }


def make_circuit() -> QuantumCircuit:
    # Qubit 2 is touched only by the barrier and has no device values.
    qc = QuantumCircuit(3)
    qc.h(0)
    qc.cx(0, 1)
    qc.barrier()
    return qc


def test_from_dict_extracts_values_used_by_circuit():
    # Test that from_dict keeps only the values the circuit uses.
    device = DeviceData.from_dict(make_device_info(), make_circuit())

    assert device == DeviceData(
        one_qubit_fidelities={0: 0.999, 1: 0.998},
        two_qubit_fidelities={(0, 1): 0.98},
        readout_errors={0: 0.01, 1: 0.02},
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda info: info["qubits"][0].pop("fidelity"),
        lambda info: info["qubits"][0].update(fidelity=1.5),
    ],
    ids=["missing_value", "out_of_range"],
)
def test_from_dict_returns_none_for_invalid_used_value(mutate):
    # Test that from_dict returns None if a value the circuit uses is invalid.
    device_info = make_device_info()
    mutate(device_info)

    assert DeviceData.from_dict(device_info, make_circuit()) is None


def test_error_properties_derived_from_fidelities():
    # Test that the error properties are correctly derived from the fidelities in DeviceData.
    device = DeviceData(
        one_qubit_fidelities={0: 0.999, 1: 0.998},
        two_qubit_fidelities={(0, 1): 0.98},
        readout_errors={0: 0.01, 1: 0.02},
    )

    assert device.one_qubit_errors == pytest.approx({0: 0.001, 1: 0.002})
    assert device.two_qubit_errors == pytest.approx({(0, 1): 0.02})
