import json
import sys
from pathlib import Path

# add python path to import app.py
sys.path.append(str(Path(__file__).resolve().parent.parent.joinpath("src")))


import pytest
import qiskit.qasm3
from qiskit import QuantumCircuit

from oqtopus_engine_combiner.app import (  # type: ignore[attr-defined]
    CircuitCombiner,
)
from oqtopus_engine_core.interfaces.combiner_interface.v1 import combiner_pb2


def test_combine_circuits_positive_1circuit():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
    ]
    max_qubits = 64
    circuit1 = QuantumCircuit(2, 2)
    circuit1.h(0)
    circuit1.x(0)
    circuit1.cx(0, 1)
    circuit1.measure(0, 0)
    circuit1.measure(1, 1)
    comb_circ = QuantumCircuit(2, 2)
    comb_circ.append(circuit1, [0, 1], [0, 1])
    comb_circ_text = qiskit.qasm3.dumps(comb_circ.decompose())
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [2]
    assert combined_program == comb_circ_text


def test_combine_circuits_positive_2circuits_clbits_qubits():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    circuit1 = QuantumCircuit(2, 2)
    circuit1.h(0)
    circuit1.x(0)
    circuit1.cx(0, 1)
    circuit1.measure(0, 0)
    circuit1.measure(1, 1)
    circuit2 = QuantumCircuit(2, 1)
    circuit2.h(0)
    circuit2.x(0)
    circuit2.cx(0, 1)
    circuit2.measure(0, 0)
    comb_circ = QuantumCircuit(4, 3)
    comb_circ.append(circuit1, [0, 1], [0, 1])
    comb_circ.append(circuit2, [2, 3], [2])
    comb_circ_text = qiskit.qasm3.dumps(comb_circ.decompose())
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [1, 2]
    assert combined_program == comb_circ_text


def test_combine_circuits_positive_3circuits():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[59] q;bit[2] cbit;\nh q[0];\nx q[0];\nx q[20];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[3] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\ncx q[1], q[2];\nx q[2];\nmeasure q[2] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    circuit1 = QuantumCircuit(59, 2)
    circuit1.h(0)
    circuit1.x(0)
    circuit1.x(20)
    circuit1.cx(0, 1)
    circuit1.measure(0, 0)
    circuit1.measure(1, 1)
    circuit2 = QuantumCircuit(2, 1)
    circuit2.h(0)
    circuit2.x(0)
    circuit2.cx(0, 1)
    circuit2.measure(0, 0)
    circuit3 = QuantumCircuit(3, 1)
    circuit3.h(0)
    circuit3.x(0)
    circuit3.cx(0, 1)
    circuit3.cx(1, 2)
    circuit3.x(2)
    circuit3.measure(2, 0)
    comb_circ = QuantumCircuit(64, 4)
    comb_circ.append(circuit1, list(range(59)), [0, 1])
    comb_circ.append(circuit2, [59, 60], [2])
    comb_circ.append(circuit3, [61, 62, 63], [3])
    comb_circ_text = qiskit.qasm3.dumps(comb_circ.decompose())
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [1, 1, 2]
    assert combined_program == comb_circ_text


def test_combine_circuits_positive_3circuits_nochange_by_deviceinfo_in_maxcubits():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[59] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[3] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\ncx q[1], q[2];\nx q[2];\nmeasure q[2] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 66
    circuit1 = QuantumCircuit(59, 2)
    circuit1.h(0)
    circuit1.x(0)
    circuit1.cx(0, 1)
    circuit1.measure(0, 0)
    circuit1.measure(1, 1)
    circuit2 = QuantumCircuit(2, 1)
    circuit2.h(0)
    circuit2.x(0)
    circuit2.cx(0, 1)
    circuit2.measure(0, 0)
    circuit3 = QuantumCircuit(3, 1)
    circuit3.h(0)
    circuit3.x(0)
    circuit3.cx(0, 1)
    circuit3.cx(1, 2)
    circuit3.x(2)
    circuit3.measure(2, 0)
    comb_circ = QuantumCircuit(64, 4)
    comb_circ.append(circuit1, list(range(59)), [0, 1])
    comb_circ.append(circuit2, [59, 60], [2])
    comb_circ.append(circuit3, [61, 62, 63], [3])
    comb_circ_text = qiskit.qasm3.dumps(comb_circ.decompose())
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [1, 1, 2]
    assert combined_program == comb_circ_text


def test_combine_circuits_positive_2circuits_set_maxcubits_negative_value():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[0] q;',
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[0] q;',
    ]
    max_qubits = -1
    circuit1 = QuantumCircuit(0, 0)
    circuit2 = QuantumCircuit(0, 0)
    comb_circ = QuantumCircuit(0, 0)
    comb_circ.append(circuit1, [], [])
    comb_circ.append(circuit2, [], [])
    comb_circ_text = qiskit.qasm3.dumps(comb_circ.decompose())

    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_program == comb_circ_text
    assert combined_qubits_list == [0, 0]


def test_combine_circuits_negative_exceeded_maxcubits():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[63] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 2
    assert combined_program is None
    assert len(combined_qubits_list) == 0


def test_combine_circuits_negative_3circuits_exceeded_modified_maxcubits():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[59] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[3] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\ncx q[1], q[2];\nx q[2];\nmeasure q[2] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 63
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 2
    assert combined_program is None
    assert len(combined_qubits_list) == 0


def test_combine_circuits_negative_qubits_shortage():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[1] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 1
    assert combined_program is None
    assert len(combined_qubits_list) == 0


def test_combine_circuits_negative_qasm_string_error():
    cc = CircuitCombiner()

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqbit[2] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    status, combined_program, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 1
    assert combined_program is None
    assert len(combined_qubits_list) == 0


def test_deal_with_request_programs_positive_qasm_str_conversion():
    cc = CircuitCombiner()

    programs = ['OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];', 'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];']  # noqa: E501
    programs_str = json.dumps(programs)
    request = combiner_pb2.CombineRequest(
        programs=programs_str,
        max_qubits=10,
    )

    json_array = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];',  # noqa: E501
    ]
    assert cc.deal_with_request_programs(request.programs) == json_array


def test_deal_with_request_programs_negative_qasm_str_anomaly():
    cc = CircuitCombiner()

    # anomaly: missing opening parenthesis
    programs_str = '"OPENQASM 3;\\ninclude \\"stdgates.inc\\";\\nqubit[2] q;\\n\\nh q[0];\\nx q[0];\\ncx q[0], q[1];", "OPENQASM 3;\\ninclude \\"stdgates.inc\\";\\nqubit[1] q;\\n\\nh q[0];\\nx q[0];"]'  # noqa: E501
    request = combiner_pb2.CombineRequest(
        programs=programs_str,
        max_qubits=10,
    )
    with pytest.raises(ValueError, match=r"invalid input program array.*"):
        cc.deal_with_request_programs(request.programs)


def test_circuit_combiner_positive():

    programs = ['OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];', 'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[1] q;\n\nh q[0];\nx q[0];']  # noqa: E501
    programs_str = json.dumps(programs)
    request = combiner_pb2.CombineRequest(
        programs=programs_str,
        max_qubits=10,
    )
    response = CircuitCombiner().Combine(request, None)
    circuit1 = QuantumCircuit(2, 0)
    circuit1.h(0)
    circuit1.x(0)
    circuit1.cx(0, 1)
    circuit2 = QuantumCircuit(1, 0)
    circuit2.h(0)
    circuit2.x(0)
    comb_circ = QuantumCircuit(3, 0)
    comb_circ.append(circuit1, [0, 1], [])
    comb_circ.append(circuit2, [2], [])
    comb_circ_text = qiskit.qasm3.dumps(comb_circ.decompose())
    assert response.combined_status == 0
    assert response.combined_program == comb_circ_text
    assert response.combined_qubits_list == [0, 0]


def test_circuit_combiner_negative_qasm_json_str_anomaly():

    # anomaly: missing opening parenthesis
    programs_str = '"OPENQASM 3;\\ninclude \\"stdgates.inc\\";\\nqubit[2] q;\\n\\nh q[0];\\nx q[0];\\ncx q[0], q[1];", "OPENQASM 3;\\ninclude \\"stdgates.inc\\";\\nqubit[1] q;\\n\\nh q[0];\\nx q[0];"]'  # noqa: E501
    request = combiner_pb2.CombineRequest(
        programs=programs_str,
        max_qubits=10,
    )
    response = CircuitCombiner().Combine(request, None)
    assert response.combined_status == 1
    assert response.combined_program == ""
    assert response.combined_qubits_list == []


def test_circuit_combiner_negative_qasm_circuit_str_anomaly():

    # anomaly: qubit -> qbit in qasm string
    programs = ['OPENQASM 3;\ninclude "stdgates.inc";\nqbit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];', 'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[1] q;\n\nh q[0];\nx q[0];']  # noqa: E501
    programs_str = json.dumps(programs)
    request = combiner_pb2.CombineRequest(
        programs=programs_str,
        max_qubits=10,
    )
    response = CircuitCombiner().Combine(request, None)
    assert response.combined_status == 1
    assert response.combined_program == ""
    assert response.combined_qubits_list == []
