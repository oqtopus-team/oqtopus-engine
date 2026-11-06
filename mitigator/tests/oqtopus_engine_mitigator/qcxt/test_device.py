import pytest

from oqtopus_engine_mitigator.qcxt.device import DeviceData


def make_device_info(*, coupling_fidelity: float = 0.98) -> dict:
    return {
        "qubits": [
            {
                "id": "0",
                "fidelity": 0.999,
                "meas_error": {"readout_assignment_error": 0.01},
            },
            {
                "id": "1",
                "fidelity": 0.998,
                "meas_error": {"readout_assignment_error": 0.02},
            },
        ],
        "couplings": [
            {"control": 0, "target": 1, "fidelity": coupling_fidelity},
        ],
    }


def test_from_dict_builds_expected_fields():
    # Test that from_dict constructs a DeviceData object with the expected fields.
    device_info = make_device_info(coupling_fidelity=0.98)

    device = DeviceData.from_dict(device_info)

    assert device.one_qubit_fidelities == {0: 0.999, 1: 0.998}
    assert device.two_qubit_fidelities == {(0, 1): 0.98}
    assert device.readout_errors == {0: 0.01, 1: 0.02}
    # id fields given as strings should be converted to int
    assert all(isinstance(qid, int) for qid in device.one_qubit_fidelities)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda info: info.pop("couplings"),
        lambda info: info["qubits"][0].update(fidelity=1.5),
        lambda info: info["couplings"][0].update(fidelity=float("nan")),
        lambda info: info["qubits"][1].update(id="0"),
    ],
    ids=["missing_key", "fidelity_out_of_range", "nan_fidelity", "duplicate_id"],
)
def test_from_dict_rejects_invalid_device_info(mutate):
    # Test that from_dict raises ValueError for malformed or out-of-range device_info.
    device_info = make_device_info()
    mutate(device_info)

    with pytest.raises(ValueError):
        DeviceData.from_dict(device_info)


def test_error_properties_derived_from_fidelities():
    # Test that the error properties are correctly derived from the fidelities in DeviceData.
    device = DeviceData(
        one_qubit_fidelities={0: 0.999, 1: 0.998},
        two_qubit_fidelities={(0, 1): 0.98},
        readout_errors={0: 0.01, 1: 0.02},
    )

    assert device.one_qubit_errors == pytest.approx({0: 0.001, 1: 0.002})
    assert device.two_qubit_errors == pytest.approx({(0, 1): 0.02})
