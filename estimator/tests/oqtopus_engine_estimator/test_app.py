import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
import cmath
import json

import numpy as np
import pytest
from qiskit import qasm3
from qiskit.circuit import QuantumCircuit
from qiskit.circuit.random import random_circuit
from qiskit.primitives import StatevectorSampler
from qiskit.providers.fake_provider import GenericBackendV2
from qiskit.quantum_info import Statevector
from qiskit.quantum_info.operators import SparsePauliOp
from qiskit.quantum_info.operators.random import random_hermitian
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

from oqtopus_engine_estimator.app import Estimator, create_qiskit_operator


@pytest.fixture(scope="session")
def estimator():
    return Estimator()


def test_create_qiskit_operator():
    # Test with spaces between label and index: "X 0 X 1"
    operators = '[["X 0 X 1", 1.5], ["Y 0 Z 1", 1.2]]'
    pauli_op = create_qiskit_operator(operators, 2)
    print(pauli_op)
    assert pauli_op.to_list()[0] == ("XX", complex(1.5, 0.0))
    assert pauli_op.to_list()[1] == ("ZY", complex(1.2, 0.0))

    # Test without spaces: "X0X1"
    operators = '[["X0X1", 1.5], ["Y0Z1", 1.2]]'
    pauli_op = create_qiskit_operator(operators, 2)
    print(pauli_op)
    assert pauli_op.to_list()[0] == ("XX", complex(1.5, 0.0))
    assert pauli_op.to_list()[1] == ("ZY", complex(1.2, 0.0))

    # Test with mixed spacing: "X 0X 1"
    operators = '[["X 0X 1", 1.5], ["Y 0Z 1", 1.2]]'
    pauli_op = create_qiskit_operator(operators, 2)
    print(pauli_op)
    assert pauli_op.to_list()[0] == ("XX", complex(1.5, 0.0))
    assert pauli_op.to_list()[1] == ("ZY", complex(1.2, 0.0))

    # Test with inconsistent spacing: "X0 X1"
    operators = '[["X0 X1", 1.5], ["Y0 Z1", 1.2]]'
    pauli_op = create_qiskit_operator(operators, 2)
    print(pauli_op)
    assert pauli_op.to_list()[0] == ("XX", complex(1.5, 0.0))
    assert pauli_op.to_list()[1] == ("ZY", complex(1.2, 0.0))


def test_preprocess(estimator):
    qasm_code = (
        'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n'
    )
    operators = '[["X 0 X 1", 1.5], ["Y 0 Z 1", 1.2]]'
    basis_gates = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
    mapping_list = []
    qasms, op = estimator._preprocess(qasm_code, operators, basis_gates, mapping_list)

    assert (
        qasms[0]
        == 'OPENQASM 3.0;\ninclude "stdgates.inc";\nbit[2] __c_XX;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\nrz(pi/2) q[0];\nsx q[0];\nrz(pi/2) q[0];\nrz(pi/2) q[1];\nsx q[1];\nrz(pi/2) q[1];\n__c_XX[0] = measure q[0];\n__c_XX[1] = measure q[1];\n'
    )
    assert (
        qasms[1]
        == 'OPENQASM 3.0;\ninclude "stdgates.inc";\nbit[2] __c_ZY;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\nsx q[0];\nrz(pi/2) q[0];\n__c_ZY[0] = measure q[0];\n__c_ZY[1] = measure q[1];\n'
    )
    assert op == '[[["XX"], ["ZY"]], [[1.5], [1.2]]]'


class counts:
    key: str
    value: int


class counts_test:
    counts: counts


def test_postprocess(estimator):
    # counts_list= [
    #    {"00": 425, "01": 75, "10": 85, "11": 415},
    #    {"00": 500, "01": 0, "10": 0, "11": 500},
    # ]

    counts_list1 = {k: v for k, v in [["00", 425], ["01", 75], ["10", 85], ["11", 415]]}
    counts_list2 = {k: v for k, v in [["00", 500], ["01", 0], ["10", 0], ["11", 500]]}
    counts1 = counts_test()
    counts2 = counts_test()
    counts1.counts = counts_list1
    counts2.counts = counts_list2
    counts_list = []
    counts_list.append(counts1)
    counts_list.append(counts2)

    op = '[[["XX"], ["ZY"]], [[1.5], [1.2]]]'
    actual_expval, actual_stds = estimator._postprocess(counts_list, op)
    expect_expval = np.float64(2.22)
    expect_stds = np.float64(0.0348)
    assert cmath.isclose(actual_expval, expect_expval, abs_tol=1e-1)
    assert cmath.isclose(actual_stds, expect_stds, abs_tol=1e-2)


def test_simple_circuits_with_random_op(estimator):
    num_qubits = 2
    basis_gates = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
    mapping_list = [0, 1]

    # prepare circuits and operators
    qasm_code = (
        'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n'
    )
    # observable = SparsePauliOp.from_list([("XX", 1.5), ("ZY", 1.2)])
    op = random_hermitian(dims=2**num_qubits, seed=0)
    observable = SparsePauliOp.from_operator(op)
    operators = translate_operator2string(observable)

    # preprocess
    preprocessed_qasms, grouped_paulis = estimator._preprocess(
        qasm_code, operators, basis_gates, mapping_list
    )

    # process
    preprocessed_qc = [
        qasm3.loads(preprocessed_qasm) for preprocessed_qasm in preprocessed_qasms
    ]
    sampler_qiskit = StatevectorSampler()
    l_counts = []
    for i, qc in enumerate(preprocessed_qc):
        pubs = [(qc)]
        job_qiskit = sampler_qiskit.run(pubs, shots=4096)
        sampler_result = job_qiskit.result()
        # l_counts.append(sampler_result[0].data[qc.cregs[0].name].get_counts())
        l_counts_test = counts_test()
        l_counts_test.counts = sampler_result[0].data[qc.cregs[0].name].get_counts()
        l_counts.append(l_counts_test)

    # postprocess
    actual_expval, actual_stds = estimator._postprocess(l_counts, grouped_paulis)

    # expect expval by statevector simulation
    circuit = qasm3.loads(qasm_code)
    statevector = Statevector(circuit)
    expect_expval = statevector.expectation_value(observable).real

    assert cmath.isclose(actual_expval, expect_expval, abs_tol=2e-1)


def test_random_circuits_with_random_op(estimator):
    num_qubits = 4
    basis_gates = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
    mapping_list = [0, 1, 2, 3]

    for i in range(1):
        circuit: QuantumCircuit = random_circuit(
            num_qubits=num_qubits, depth=10, seed=i
        ).decompose()

        op = random_hermitian(dims=2**num_qubits, seed=i)
        observable = SparsePauliOp.from_operator(op)
        qasm_code = qasm3.dumps(circuit)
        operators = translate_operator2string(observable)

        # preprocess
        preprocessed_qasms, grouped_operators = estimator._preprocess(
            qasm_code, operators, basis_gates, mapping_list
        )

        # process
        preprocessed_qc = [
            qasm3.loads(preprocessed_qasm) for preprocessed_qasm in preprocessed_qasms
        ]
        sampler_qiskit = StatevectorSampler()
        l_counts = []
        for i, qc in enumerate(preprocessed_qc):
            pubs = [(qc)]
            job_qiskit = sampler_qiskit.run(pubs, shots=10000)
            sampler_result = job_qiskit.result()
            # l_counts.append(sampler_result[0].data[qc.cregs[0].name].get_counts())
            l_counts_test = counts_test()
            l_counts_test.counts = sampler_result[0].data[qc.cregs[0].name].get_counts()
            l_counts.append(l_counts_test)

        # postprocess
        actual_expval, actual_stds = estimator._postprocess(l_counts, grouped_operators)

        # expectation by statevector simulation
        statevector = Statevector(circuit)
        expect_expval = statevector.expectation_value(observable).real

        assert cmath.isclose(actual_expval, expect_expval, abs_tol=2e-1)


def test_random_circuits_with_transpiler(estimator):
    num_qubits = 4
    basis_gates = ["rzx", "id", "rz", "sx", "reset", "delay", "measure"]
    backend = GenericBackendV2(num_qubits=10, basis_gates=basis_gates)

    for i in range(1):
        circuit: QuantumCircuit = random_circuit(
            num_qubits=num_qubits, depth=10, seed=i
        )
        pm = generate_preset_pass_manager(optimization_level=2, backend=backend)
        transpiled_qc = pm.run(circuit)
        layout = transpiled_qc.layout
        mapping_list = layout.final_index_layout(filter_ancillas=True)

        op = random_hermitian(dims=2**num_qubits, seed=i)
        observable = SparsePauliOp.from_operator(op)
        qasm_code = qasm3.dumps(transpiled_qc)
        operators = translate_operator2string(observable)

        # preprocess
        preprocessed_qasms, grouped_operators = estimator._preprocess(
            qasm_code, operators, basis_gates, mapping_list
        )

        # process
        preprocessed_qc = [
            qasm3.loads(preprocessed_qasm) for preprocessed_qasm in preprocessed_qasms
        ]
        sampler_qiskit = StatevectorSampler()
        l_counts = []
        for i, qc in enumerate(preprocessed_qc):
            pubs = [(qc)]
            job_qiskit = sampler_qiskit.run(pubs, shots=10000)
            sampler_result = job_qiskit.result()
            # l_counts.append(sampler_result[0].data[qc.cregs[0].name].get_counts())
            l_counts_test = counts_test()
            l_counts_test.counts = sampler_result[0].data[qc.cregs[0].name].get_counts()
            l_counts.append(l_counts_test)

        # postprocess
        actual_expval, actual_stds = estimator._postprocess(l_counts, grouped_operators)

        # expect expval by statevector simulation
        statevector = Statevector(circuit.decompose())
        expect_expval = statevector.expectation_value(observable).real

        assert cmath.isclose(actual_expval, expect_expval, abs_tol=2e-1)


def translate_operator2string(op: SparsePauliOp) -> str:
    output = []
    for term in op.to_list():
        orig_operator = term[0]
        list_operator = list(orig_operator)
        list_operator.reverse()
        operator = ""
        for i, pauli in enumerate(list_operator):
            operator += " ".join([pauli, str(i)]) + " "
        operator = operator.rstrip()
        coefficient = term[1]
        body = [operator, coefficient.real]
        output.append(body)
    return json.dumps(output)
