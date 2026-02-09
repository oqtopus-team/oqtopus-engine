import cmath

import numpy as np
import pytest
from qiskit import qasm3
from qiskit.primitives import BackendEstimatorV2, StatevectorSampler
from qiskit.providers.fake_provider import GenericBackendV2
from qiskit.quantum_info.operators import SparsePauliOp
from qiskit.quantum_info.operators.random import random_hermitian
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_aer.backends import AerSimulator

from oqtopus_engine_core.framework.model import OperatorItem
from oqtopus_engine_core.steps import EstimatorStep


@pytest.fixture
def estimator_step_instance():
    return EstimatorStep()


def test_create_qiskit_operator(
    estimator_step_instance: EstimatorStep,
):
    operators = [
        OperatorItem(pauli="X 0 X 1", coeff=1.5),
        OperatorItem(pauli="Y 0 Z 1", coeff=1.2),
    ]
    pauli_op = estimator_step_instance.create_qiskit_operator(operators, 2)
    assert pauli_op.to_list()[0] == ("XX", complex(1.5, 0.0))
    assert pauli_op.to_list()[1] == ("ZY", complex(1.2, 0.0))

    operators = [
        OperatorItem(pauli="X0 X1", coeff=1.5),
        OperatorItem(pauli="Y0 Z1", coeff=1.2),
    ]
    pauli_op = estimator_step_instance.create_qiskit_operator(operators, 3)
    assert pauli_op.to_list()[0] == ("IXX", complex(1.5, 0.0))
    assert pauli_op.to_list()[1] == ("IZY", complex(1.2, 0.0))

    operators = [
        OperatorItem(pauli="I", coeff=2.0),
    ]
    pauli_op = estimator_step_instance.create_qiskit_operator(
        operators=operators, n_qubits=5
    )
    assert pauli_op.to_list()[0] == ("IIIII", complex(2.0, 0.0))


def test__pre_process(
    estimator_step_instance: EstimatorStep,
):
    # Prepare a simple QASM and operator
    qasm_code = """
    OPENQASM 3;
    include "stdgates.inc";
    qubit[2] q;
    x q[0];
    """
    input_operators = [OperatorItem(pauli="X 0", coeff=1.0)]
    virtual_physical_mapping = {"0": 1, "1": 0}
    basis_gates = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
    actual_preprocessed_qasms, actual_grouped_operators = (
        estimator_step_instance._pre_process(
            qasm_code=qasm_code,
            operators=input_operators,
            virtual_physical_mapping=virtual_physical_mapping,
            basis_gates=basis_gates,
        )
    )
    expected_qasm = 'OPENQASM 3.0;\ninclude "stdgates.inc";\nbit[1] __c_XI;\nqubit[2] q;\nx q[0];\nrz(pi/2) q[1];\nsx q[1];\nrz(pi/2) q[1];\n__c_XI[0] = measure q[1];\n'
    expected_operator = [[["X"]], [[1.0]]]
    assert actual_preprocessed_qasms[0] == expected_qasm
    assert actual_grouped_operators == expected_operator


def test__post_process(
    estimator_step_instance: EstimatorStep,
):
    counts_list: list[dict[str, int]] = [
        {"0000": 500, "0100": 0, "1000": 0, "1100": 500},
        {"0000": 250, "0100": 250, "1000": 250, "1100": 250},
    ]
    input_operator = [[["XXII"], ["YZII"]], [[1.5], [1.2]]]
    actual_expval, actual_stds = estimator_step_instance._post_process(
        counts_list, input_operator
    )

    # Check that the result was set correctly
    expect_expval = np.float64(1.5)
    expect_stds = np.float64(0.0)
    assert cmath.isclose(actual_expval, expect_expval, abs_tol=1e-1)
    assert cmath.isclose(actual_stds, expect_stds, abs_tol=1e-1)


def test_simple_circuits_with_random_op(
    estimator_step_instance: EstimatorStep,
):
    """Test estimation step with a simple circuit and a random operator."""
    num_qubits = 2
    basis_gates = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]

    # Prepare a simple QASM and operator
    qasm_code = (
        'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n'
    )
    op = random_hermitian(dims=2**num_qubits, seed=1)
    observable = SparsePauliOp.from_operator(op)
    operators = [
        OperatorItem(pauli=body[0], coeff=body[1])
        for body in translate_operator2string(observable)
    ]
    # observable = SparsePauliOp.from_list([("XX", 1.5), ("ZY", 1.2)])
    # operators = [
    #     OperatorItem(pauli="X 0 X 1", coeff=1.5),
    #     OperatorItem(pauli="Y 0 Z 1", coeff=1.2),
    # ]
    # transpile
    backend = GenericBackendV2(num_qubits=10, basis_gates=basis_gates)
    pm = generate_preset_pass_manager(optimization_level=2, backend=backend)
    transpiled_qc = pm.run(qasm3.loads(qasm_code))
    layout = transpiled_qc.layout
    mapping_list = layout.final_index_layout(filter_ancillas=True)
    virtual_physical_mapping = {
        str(i): mapping_list[i] for i in range(len(mapping_list))
    }

    # preprocess
    preprocessed_qasms, grouped_paulis = estimator_step_instance._pre_process(
        qasm_code=qasm3.dumps(transpiled_qc),
        operators=operators,
        basis_gates=basis_gates,
        virtual_physical_mapping=virtual_physical_mapping,
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
        l_counts_test = sampler_result[0].data[qc.cregs[0].name].get_counts()
        l_counts.append(l_counts_test)

    # postprocess
    actual_expval, actual_stds = estimator_step_instance._post_process(
        l_counts, grouped_paulis
    )

    # expect expval by qiskit AerSimulator based Estimator
    pubs = [(qasm3.loads(qasm_code), observable)]
    aer_backend = AerSimulator()
    estimator_qiskit = BackendEstimatorV2(backend=aer_backend)
    job_qiskit = estimator_qiskit.run(pubs)
    estimator_result = job_qiskit.result()
    expect_expval = estimator_result[0].data["evs"]
    expect_stds = estimator_result[0].data["stds"]

    assert cmath.isclose(actual_expval, expect_expval, abs_tol=2e-1)
    assert cmath.isclose(actual_stds, expect_stds, abs_tol=1e-1)


def test_create_qiskit_operator_invalid_operator(
    estimator_step_instance: EstimatorStep,
):
    # Test that ValueError is raised for invalid operator
    operators = [
        OperatorItem(pauli="Q0", coeff=1.0),  # Invalid Pauli label
    ]
    with pytest.raises(ValueError, match="The specified operator is invalid"):
        estimator_step_instance.create_qiskit_operator(operators, 1)


def test_create_qiskit_operator_identity_with_index(
    estimator_step_instance: EstimatorStep,
):
    # Test that 'I' with index is handled correctly
    operators = [
        OperatorItem(pauli="I 0", coeff=3.0),
    ]
    pauli_op = estimator_step_instance.create_qiskit_operator(operators, 2)
    assert pauli_op.to_list()[0][0] == "II"
    assert pauli_op.to_list()[0][1] == complex(3.0, 0.0)


def test__post_process_multiple_counts(estimator_step_instance):
    # Test _post_process with multiple counts and operators
    counts_list = [
        {"0": 100, "1": 0},
        {"0": 50, "1": 50},
    ]
    grouped_operators = [[["X"], ["Z"]], [[1.0], [2.0]]]
    expval, stds = estimator_step_instance._post_process(
        counts_list, grouped_operators
    )
    assert isinstance(expval, (float, np.floating))
    assert isinstance(stds, (float, np.floating))


def test__pre_process_no_virtual_physical_mapping(
    estimator_step_instance: EstimatorStep,
):
    # Test _pre_process with no virtual_physical_mapping
    qasm_code = """
    OPENQASM 3;
    include "stdgates.inc";
    qubit[2] q;
    x q[0];
    """
    input_operators = [OperatorItem(pauli="X 0", coeff=1.0)]
    basis_gates = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
    preprocessed_qasms, grouped_operators = estimator_step_instance._pre_process(
        qasm_code=qasm_code,
        operators=input_operators,
        virtual_physical_mapping=None,
        basis_gates=basis_gates,
    )
    assert isinstance(preprocessed_qasms, list)
    assert isinstance(grouped_operators, list)
    assert preprocessed_qasms
    assert grouped_operators


def test__pre_process_virtual_physical_mapping_extra(estimator_step_instance):
    # Test _pre_process with virtual_physical_mapping that has missing indices
    qasm_code = """
    OPENQASM 3;
    include "stdgates.inc";
    qubit[3] q;
    x q[0];
    """
    input_operators = [OperatorItem(pauli="X 0", coeff=1.0)]
    virtual_physical_mapping = {"0": 2, "1": 0}  # missing "2"
    basis_gates = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]
    preprocessed_qasms, grouped_operators = estimator_step_instance._pre_process(
        qasm_code=qasm_code,
        operators=input_operators,
        virtual_physical_mapping=virtual_physical_mapping,
        basis_gates=basis_gates,
    )
    assert isinstance(preprocessed_qasms, list)
    assert isinstance(grouped_operators, list)
    assert preprocessed_qasms
    assert grouped_operators


def translate_operator2string(op: SparsePauliOp) -> list:
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
    return output
