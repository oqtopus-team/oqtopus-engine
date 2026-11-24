import math

import pytest
from qiskit import QuantumCircuit  # type: ignore[import-untyped]

from oqtopus_engine_mitigator.qcxt.device import DeviceData
from oqtopus_engine_mitigator.qcxt.simulator import Simulator


def make_bell_circuit() -> QuantumCircuit:
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    return qc


def make_device() -> DeviceData:
    return DeviceData(
        one_qubit_fidelities={0: 0.999, 1: 0.998},
        two_qubit_fidelities={(0, 1): 0.98},
        readout_errors={0: 0.01, 1: 0.02},
    )


def test_calculate_expval_deterministic_ideal():
    # Test that the simulator returns the expected expval for a Bell state in a noiseless simulation.
    simulator = Simulator(device=None)
    qc = make_bell_circuit()

    expval = simulator.calculate_expval(
        [qc], shots=1000, coefficients=None, is_noisy_simulated=False
    )

    # ZZ parity measurement on a Bell state is always even, so the expval should be +1.
    assert expval == pytest.approx(1.0, abs=1e-9)


def test_calculate_expval_with_coefficients():
    # Test that the simulator computes expectation value when multiple circuits and coefficients are provided.
    simulator = Simulator(device=None)
    circuit_plus_one = QuantumCircuit(1)
    circuit_plus_one.id(0)  # stays |0>, so expected parity is +1
    circuit_minus_one = QuantumCircuit(1)
    circuit_minus_one.x(0)  # flips to |1>, so expected parity is -1

    expval = simulator.calculate_expval(
        [circuit_plus_one, circuit_minus_one],
        shots=100,
        coefficients=[0.6, 0.4],
        is_noisy_simulated=False,
    )

    assert expval == pytest.approx(0.6 * 1.0 + 0.4 * (-1.0), abs=1e-9)


def test_calculate_expval_noisy_runs():
    # Test that the simulator returns a valid expectation value in a noisy simulation.
    simulator = Simulator(device=make_device())
    qc = make_bell_circuit()

    expval = simulator.calculate_expval([qc], shots=200)

    assert -1.0 <= expval <= 1.0


def test_calculate_expval_appends_measurement_when_absent():
    # Test that the simulator automatically appends a measurement layer if the circuit has no measurement instructions.
    simulator = Simulator(device=None)
    qc = QuantumCircuit(1)
    qc.x(0)  # no measurement instruction

    expval = simulator.calculate_expval([qc], shots=50, is_noisy_simulated=False)

    assert expval == pytest.approx(-1.0, abs=1e-9)


@pytest.mark.parametrize(
    "apply_z",
    [
        lambda qc: qc.rz(math.pi, 0),
        lambda qc: qc.z(0),
    ],
    ids=["rz", "z"],
)
def test_calculate_expval_simulates_z_in_any_gate_form(apply_z):
    # Test that Z is simulated whatever gate expresses it (e.g. rz(pi) in a cut term).
    simulator = Simulator(device=None)
    qc = QuantumCircuit(1)
    qc.h(0)
    apply_z(qc)
    qc.h(0)

    expval = simulator.calculate_expval([qc], shots=100, is_noisy_simulated=False)

    # H Z H = X flips |0> to |1>, so the expval should be -1.
    assert expval == pytest.approx(-1.0, abs=1e-9)


def test_judge_true_when_cut_closer_to_ideal():
    # Test that the judge method returns True when the result is improved by applying the cut.
    assert Simulator.judge(expval_ideal=1.0, expval_std=0.5, expval_cut=0.9) is True


def test_judge_false_when_cut_not_closer():
    # Test that the judge method returns False when the cut does not improve the result.
    assert Simulator.judge(expval_ideal=1.0, expval_std=0.9, expval_cut=0.9) is False
    assert Simulator.judge(expval_ideal=1.0, expval_std=0.9, expval_cut=0.5) is False
