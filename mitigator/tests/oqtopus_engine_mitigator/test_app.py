import random

import numpy as np
import pytest
from pydantic.dataclasses import dataclass
from qiskit import QuantumCircuit, qasm3
from qiskit.result import QuasiDistribution

from oqtopus_engine_mitigator.app import ErrorMitigator, get_measured_qubits


@dataclass
class MesError:
    p0m1: float
    p1m0: float


@dataclass
class Qubit:
    id: int
    t1: float
    t2: float
    gate_error: float
    mes_error: MesError


@dataclass
class DeviceTopology:
    name: str
    qubits: list[Qubit]


@pytest.fixture(scope="session")
def error_mitigator():
    return ErrorMitigator()


def test_get_measured_qubits_multiple_measures():
    # Patch qasm3.loads to return a mock object with multiple measure instructions
    program = 'OPENQASM 3.0; include "stdgates.inc"; bit[5] c; rz(pi/2) $3; sx $3; rz(pi/2) $3; rz(pi/2) $4; sx $4; rz(pi/2) $4; rz(pi/2) $7; sx $7; rz(pi/2) $7; cx $7, $5; rz(pi/2) $5; sx $5; rz(pi/2) $5; cx $4, $5; rz(pi/2) $4; sx $4; rz(pi/2) $4; cx $4, $1; rz(pi/2) $1; sx $1; rz(pi/2) $1; cx $3, $1; rz(pi/2) $1; sx $1; rz(pi/2) $1; rz(pi/2) $3; sx $3; rz(pi/2) $3; rz(pi/2) $5; sx $5; rz(pi/2) $5; c[0] = measure $7; c[1] = measure $5; c[2] = measure $4; c[3] = measure $1; c[4] = measure $3;'

    actual = get_measured_qubits(program)
    expected = [7, 5, 4, 1, 3]
    assert actual == expected


def test_ro_error_mitigation(error_mitigator):
    tolerance = 10
    device_topology = {
        "name": "anemone",
        "device_id": "anemone",
        "qubits": [
            {
                "id": 0,
                "physical_id": 16,
                "position": {"x": 0, "y": 8.88888888888889},
                "fidelity": 0.9965841095863902,
                "meas_error": {
                    "prob_meas1_prep0": 0.3323974609375,
                    "prob_meas0_prep1": 0.3970947265625,
                    "readout_assignment_error": 0.3629150390625,
                },
                "qubit_lifetime": {"t1": 2.5107063841533397, "t2": 5.011364719680054},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
            {
                "id": 1,
                "physical_id": 17,
                "position": {"x": 2.222222222222222, "y": 8.88888888888889},
                "fidelity": 0.999324072611772,
                "meas_error": {
                    "prob_meas1_prep0": 0.16162109375,
                    "prob_meas0_prep1": 0.207275390625,
                    "readout_assignment_error": 0.329345703125,
                },
                "qubit_lifetime": {"t1": 24.581372235073815, "t2": 11.176589609792144},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
            {
                "id": 2,
                "physical_id": 18,
                "position": {"x": 0, "y": 6.666666666666667},
                "fidelity": 0.9996653335131422,
                "meas_error": {
                    "prob_meas1_prep0": 0.154052734375,
                    "prob_meas0_prep1": 0.1993408203125,
                    "readout_assignment_error": 0.1722412109375,
                },
                "qubit_lifetime": {"t1": 22.69731343568506, "t2": 15.019474007535617},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
            {
                "id": 3,
                "physical_id": 19,
                "position": {"x": 2.222222222222222, "y": 6.666666666666667},
                "fidelity": 0.9991322438175152,
                "meas_error": {
                    "prob_meas1_prep0": 0.2884521484375,
                    "prob_meas0_prep1": 0.3282470703125,
                    "readout_assignment_error": 0.3155517578125,
                },
                "qubit_lifetime": {"t1": 24.72214845485995, "t2": 14.64287047050684},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
            {
                "id": 4,
                "physical_id": 20,
                "position": {"x": 3.3333333333333335, "y": 8.88888888888889},
                "fidelity": 0.9992133717596725,
                "meas_error": {
                    "prob_meas1_prep0": 0.2703857421875,
                    "prob_meas0_prep1": 0.2750244140625,
                    "readout_assignment_error": 0.2913818359375,
                },
                "qubit_lifetime": {"t1": 16.720393872775432, "t2": 17.291874372678198},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
            {
                "id": 5,
                "physical_id": 21,
                "position": {"x": 5.555555555555555, "y": 8.88888888888889},
                "fidelity": 0.9991630703335777,
                "meas_error": {
                    "prob_meas1_prep0": 0.099365234375,
                    "prob_meas0_prep1": 0.1490478515625,
                    "readout_assignment_error": 0.13604736328125,
                },
                "qubit_lifetime": {"t1": 34.41207265698719, "t2": 10.045905324013084},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
            {
                "id": 6,
                "physical_id": 22,
                "position": {"x": 3.3333333333333335, "y": 6.666666666666667},
                "fidelity": 0.9986489068293254,
                "meas_error": {
                    "prob_meas1_prep0": 0.165771484375,
                    "prob_meas0_prep1": 0.335205078125,
                    "readout_assignment_error": 0.230224609375,
                },
                "qubit_lifetime": {"t1": 13.043140067514669, "t2": 8.109249038330857},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
            {
                "id": 7,
                "physical_id": 23,
                "position": {"x": 5.555555555555555, "y": 6.666666666666667},
                "fidelity": 0.9981263105849967,
                "meas_error": {
                    "prob_meas1_prep0": 0.1497802734375,
                    "prob_meas0_prep1": 0.2210693359375,
                    "readout_assignment_error": 0.1826171875,
                },
                "qubit_lifetime": {"t1": 3.176197834631957, "t2": 6.294820561830519},
                "gate_duration": {"rz": 0, "sx": 16, "x": 24},
            },
        ],
        "couplings": [
            {
                "control": 0,
                "target": 1,
                "fidelity": 0.9682302660139286,
                "gate_duration": {"rzx90": 96},
            },
            {
                "control": 0,
                "target": 2,
                "fidelity": 0.9565445524490949,
                "gate_duration": {"rzx90": 112},
            },
            {
                "control": 3,
                "target": 6,
                "fidelity": 0.9258358706044788,
                "gate_duration": {"rzx90": 112},
            },
            {
                "control": 3,
                "target": 1,
                "fidelity": 0.9738254159121394,
                "gate_duration": {"rzx90": 80},
            },
            {
                "control": 3,
                "target": 2,
                "fidelity": 0.9334396602126768,
                "gate_duration": {"rzx90": 112},
            },
            {
                "control": 4,
                "target": 6,
                "fidelity": 0.9301881075382838,
                "gate_duration": {"rzx90": 128},
            },
            {
                "control": 4,
                "target": 5,
                "fidelity": 0.9744529843815672,
                "gate_duration": {"rzx90": 112},
            },
            {
                "control": 4,
                "target": 1,
                "fidelity": 0.9688870005185481,
                "gate_duration": {"rzx90": 96},
            },
            {
                "control": 7,
                "target": 5,
                "fidelity": 0.9918206269664449,
                "gate_duration": {"rzx90": 96},
            },
            {
                "control": 7,
                "target": 6,
                "fidelity": 0.9117222527155628,
                "gate_duration": {"rzx90": 112},
            },
        ],
        "calibrated_at": "2025-07-01T08:50:26.567095+09:00",
    }
    qubits = [
        Qubit(
            id=device_topology["qubits"][i]["id"],
            t1=device_topology["qubits"][i]["qubit_lifetime"]["t1"] * 1e-6,
            t2=device_topology["qubits"][i]["qubit_lifetime"]["t2"] * 1e-6,
            gate_error=device_topology["qubits"][i]["fidelity"],
            mes_error=MesError(
                p0m1=device_topology["qubits"][i]["meas_error"]["prob_meas1_prep0"],
                p1m0=device_topology["qubits"][i]["meas_error"]["prob_meas0_prep1"],
            ),
        )
        for i in range(8)
    ]
    device_topology = DeviceTopology(name="sc", qubits=qubits)
    counts = {
        "01011": 13,
        "00010": 24,
        "01100": 34,
        "10111": 20,
        "11111": 62,
        "01101": 24,
        "10110": 24,
        "00000": 122,
        "10001": 21,
        "00100": 44,
        "10101": 12,
        "11100": 37,
        "00011": 28,
        "01111": 36,
        "10011": 13,
        "11101": 31,
        "00110": 19,
        "01110": 39,
        "01000": 38,
        "00101": 11,
        "10000": 80,
        "11011": 24,
        "01010": 20,
        "01001": 13,
        "11010": 24,
        "11110": 63,
        "10010": 21,
        "10100": 29,
        "00001": 35,
        "00111": 13,
        "11000": 34,
        "11001": 16,
    }
    program = 'OPENQASM 3.0; include "stdgates.inc"; bit[5] c; rz(pi/2) $3; sx $3; rz(pi/2) $3; rz(pi/2) $4; sx $4; rz(pi/2) $4; rz(pi/2) $7; sx $7; rz(pi/2) $7; cx $7, $5; rz(pi/2) $5; sx $5; rz(pi/2) $5; cx $4, $5; rz(pi/2) $4; sx $4; rz(pi/2) $4; cx $4, $1; rz(pi/2) $1; sx $1; rz(pi/2) $1; cx $3, $1; rz(pi/2) $1; sx $1; rz(pi/2) $1; rz(pi/2) $3; sx $3; rz(pi/2) $3; rz(pi/2) $5; sx $5; rz(pi/2) $5; c[0] = measure $7; c[1] = measure $5; c[2] = measure $4; c[3] = measure $1; c[4] = measure $3;'
    mitigated_counts = error_mitigator.ro_error_mitigation(
        device_topology, counts, program
    )
    assert abs(mitigated_counts["00000"] - 288) <= tolerance
    assert abs(mitigated_counts["11111"] - 178) <= tolerance


def test_ro_error_mitigation_random(error_mitigator):
    # set test parameters
    num_qubits = 64
    num_measure = 5
    shots = 10000
    min_p0m1 = 0.8
    min_p1m0 = 0.9
    tolerance = 1

    # prepare 64 qubits topology
    qubits = [
        Qubit(
            id=i,
            t1=100e-6,
            t2=100e-6,
            gate_error=1e-2,
            mes_error=MesError(
                p0m1=random.uniform(min_p0m1, 1.0), p1m0=random.uniform(min_p1m0, 1.0)
            ),
        )
        for i in range(num_qubits)
    ]
    device_topology = DeviceTopology(name="sc", qubits=qubits)

    # prepare random counts follow normal distribution
    keys = np.array([f"{i:b}".zfill(num_measure) for i in range(2**num_measure)])
    norm_random = np.random.normal(0, 1, shots)
    values = np.histogram(norm_random, 2**num_measure)[0]
    counts = dict(zip(keys, values, strict=False))

    # prepare measured_qubits
    qc = QuantumCircuit(num_qubits, num_measure)

    # choose random measurement
    measured = np.random.choice(num_qubits, size=num_measure, replace=False)

    # measure selected qubits
    for idx, q in enumerate(measured):
        qc.measure(q, idx)
    program = qasm3.dumps(qc)

    # call Error Mitigation function
    mitigated_counts = error_mitigator.ro_error_mitigation(
        device_topology, counts, program
    )

    # calculate excpect counts
    expected_mmat = 1
    for i in get_measured_qubits(program):
        amat = np.array(
            [
                [
                    1 - qubits[i].mes_error.p0m1,
                    qubits[i].mes_error.p1m0,
                ],
                [
                    qubits[i].mes_error.p0m1,
                    1 - qubits[i].mes_error.p1m0,
                ],
            ],
            dtype=float,
        )
        try:
            mmat = np.linalg.inv(amat)
        except np.linalg.LinAlgError:
            mmat = np.linalg.pinv(amat)
        expected_mmat = np.kron(mmat, expected_mmat)

    expected_values = np.dot(expected_mmat, np.array(values))
    expected_quasi_counts = dict(zip(keys, expected_values, strict=False))
    quasi_dist = QuasiDistribution(expected_quasi_counts, shots)
    nearest_prob = quasi_dist.nearest_probability_distribution()
    expected_counts = nearest_prob.binary_probabilities(num_bits=num_measure)

    # assertion
    for key in mitigated_counts.keys():
        assert abs(expected_counts[key] - mitigated_counts[key]) <= tolerance
