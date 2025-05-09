import os
import random
import sys

import numpy as np
from qiskit.result import QuasiDistribution

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import logging

import pytest
from mitigator import (
    ErrorMitigator,
)
from pydantic.dataclasses import dataclass


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


logger = None


# initialize
@pytest.fixture(scope="session", autouse=True)
def init_error_mitigator_instance():
    global em
    global logger
    logger = logging.getLogger(__name__)
    em = ErrorMitigator(logger)
    yield
    logger = None
    em = None


def test_ro_error_mitigation():
    qubits = [
        Qubit(
            id=i,
            t1=100e-6,
            t2=100e-6,
            gate_error=1e-2,
            mes_error=MesError(p0m1=0.05, p1m0=0.05),
        )
        for i in range(4)
    ]
    device_topology = DeviceTopology(name="sc", qubits=qubits)
    counts = {k: v for k, v in [["00", 425], ["01", 75], ["10", 85], ["11", 415]]}
    shots = 1000
    measured_qubit = [0, 1]
    mitigated_counts = em.ro_error_mitigation(
        device_topology, counts, shots, measured_qubit
    )
    print(mitigated_counts)
    assert mitigated_counts["00"] == 465
    assert mitigated_counts["01"] == 34
    assert mitigated_counts["10"] == 45
    assert mitigated_counts["11"] == 454


def test_ro_error_mitigation_random():
    # set test parameters
    num_qubits = 10
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
        for i in range(64)
    ]
    device_topology = DeviceTopology(name="sc", qubits=qubits)

    # prepare random counts follow normal distribution
    keys = np.array([bin(i)[2:].zfill(num_qubits) for i in range(2**num_qubits)])
    norm_random = np.random.normal(0, 1, shots)
    values = np.histogram(norm_random, 2**num_qubits)[0]
    counts = dict(zip(keys, values))

    # prepare measured_qubits
    measured_qubits = np.random.choice(np.arange(64), size=num_qubits, replace=False)

    # call Error Mitigation function
    mitigated_counts = em.ro_error_mitigation(
        device_topology, counts, sum(counts.values()), measured_qubits
    )

    # calculate excpect counts
    expected_mmat = 1
    for i in range(num_qubits):
        amat = np.array(
            [
                [
                    1 - qubits[measured_qubits[i]].mes_error.p0m1,
                    qubits[measured_qubits[i]].mes_error.p1m0,
                ],
                [
                    qubits[measured_qubits[i]].mes_error.p0m1,
                    1 - qubits[measured_qubits[i]].mes_error.p1m0,
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
    expected_quasi_counts = dict(zip(keys, expected_values))
    quasi_dist = QuasiDistribution(expected_quasi_counts, shots)
    nearest_prob = quasi_dist.nearest_probability_distribution()
    expected_counts = nearest_prob.binary_probabilities(num_bits=num_qubits)

    # assertion
    for key in mitigated_counts.keys():
        assert abs(expected_counts[key] - mitigated_counts[key]) <= tolerance
