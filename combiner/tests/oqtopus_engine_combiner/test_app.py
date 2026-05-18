import datetime
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# add python path to import app.py
sys.path.append(str(Path(__file__).resolve().parent.parent.joinpath("src")))


import pytest
import qiskit.qasm3  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from qiskit import QuantumCircuit  # type: ignore[import-untyped]

from oqtopus_engine_combiner.app import (  # type: ignore[attr-defined]
    CircuitCombiner,
    CustomTimedRotatingFileHandler,
    InvalidQubitsError,
    _parse_args,
    assign_environ,
    serve,
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
    assert combined_qubits_list == [2, 1]
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
    assert combined_qubits_list == [2, 1, 1]
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
    assert combined_qubits_list == [2, 1, 1]
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


# ===================================================================
# Tests for OptimalCombine
# ===================================================================


def _make_linear_topology(n_qubits):
    """Create a linear topology JSON with n_qubits connected in a chain."""
    qubits = [{"id": i, "position": {"x": i, "y": 0}} for i in range(n_qubits)]
    couplings = [{"control": i, "target": i + 1} for i in range(n_qubits - 1)]
    return {"qubits": qubits, "couplings": couplings}


SIMPLE_2Q_QASM = (
    'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[2] cbit;\n'
    "h q[0];\nx q[0];\ncx q[0], q[1];\n"
    "measure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];"
)

SIMPLE_1Q_QASM = (
    'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[1] q;bit[1] cbit;\n'
    "h q[0];\nmeasure q[0] -> cbit[0];"
)


def _make_optimal_combine_request(jobs, topology):
    """Helper to create an OptimalCombineRequest."""
    programs_str = json.dumps(jobs)
    device_info_str = json.dumps(topology)
    return combiner_pb2.OptimalCombineRequest(
        programs=programs_str,
        device_info=device_info_str,
    )


def test_optimal_combine_positive_single_circuit():
    topology = _make_linear_topology(10)
    jobs = [{"job_id": "job-1", "program": SIMPLE_2Q_QASM}]
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 0  # STATUS_SUCCESS
    result = json.loads(response.combine_result)
    assert "job-1" in result["assigned_ids"]
    assert len(result["combined_groups"]) == 1
    assert "OPENQASM" in result["combined_groups"][0]["combined_program"]


def test_optimal_combine_positive_multiple_circuits():
    topology = _make_linear_topology(10)
    jobs = [
        {"job_id": "job-1", "program": SIMPLE_2Q_QASM},
        {"job_id": "job-2", "program": SIMPLE_1Q_QASM},
    ]
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 0
    result = json.loads(response.combine_result)
    assert "job-1" in result["assigned_ids"]
    assert "job-2" in result["assigned_ids"]
    assert len(result["combined_groups"]) >= 1


def test_optimal_combine_positive_combine_info_structure():
    topology = _make_linear_topology(10)
    jobs = [{"job_id": "job-1", "program": SIMPLE_2Q_QASM}]
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    result = json.loads(response.combine_result)
    group = result["combined_groups"][0]
    info = group["combine_info"]
    assert "assigned_ids" in info
    assert "assigned_group" in info
    assert "combined_qubits_list" in info
    assert "n_total_qubits" in info
    assert info["assigned_ids"] == ["job-1"]
    assert info["n_total_qubits"] >= 2


def test_optimal_combine_positive_qubit_mapping_in_assigned_group():
    topology = _make_linear_topology(10)
    jobs = [{"job_id": "job-1", "program": SIMPLE_2Q_QASM}]
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    result = json.loads(response.combine_result)
    assigned_group = result["combined_groups"][0]["combine_info"]["assigned_group"]
    assert len(assigned_group) == 1
    assert assigned_group[0]["job_id"] == "job-1"
    assert assigned_group[0]["qubit_mapping"] is not None


def test_optimal_combine_positive_circuit_too_large_unassigned():
    # Topology with 1 qubit, circuit needs 2 connected qubits
    topology = {"qubits": [{"id": 0, "position": {"x": 0, "y": 0}}], "couplings": []}
    jobs = [{"job_id": "job-1", "program": SIMPLE_2Q_QASM}]
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 0  # still SUCCESS, just no assignments
    result = json.loads(response.combine_result)
    assert "job-1" not in result["assigned_ids"]
    assert len(result["combined_groups"]) == 0


def test_optimal_combine_positive_mixed_assignable_and_unassignable():
    # 1-qubit circuit fits on 1-node topology, 2-qubit circuit does not
    topology = {"qubits": [{"id": 0, "position": {"x": 0, "y": 0}}], "couplings": []}
    jobs = [
        {"job_id": "job-1q", "program": SIMPLE_1Q_QASM},
        {"job_id": "job-2q", "program": SIMPLE_2Q_QASM},
    ]
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 0
    result = json.loads(response.combine_result)
    assert "job-1q" in result["assigned_ids"]
    assert "job-2q" not in result["assigned_ids"]


def test_optimal_combine_positive_empty_jobs():
    topology = _make_linear_topology(10)
    jobs = []
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 0
    result = json.loads(response.combine_result)
    assert result["assigned_ids"] == []
    assert result["combined_groups"] == []


def test_optimal_combine_negative_invalid_programs_json():
    # Invalid JSON for programs
    device_info_str = json.dumps(_make_linear_topology(10))
    request = combiner_pb2.OptimalCombineRequest(
        programs="not valid json [",
        device_info=device_info_str,
    )

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 1  # STATUS_FAILURE
    assert response.combine_result == ""


def test_optimal_combine_negative_invalid_device_info_json():
    jobs = [{"job_id": "job-1", "program": SIMPLE_2Q_QASM}]
    programs_str = json.dumps(jobs)
    request = combiner_pb2.OptimalCombineRequest(
        programs=programs_str,
        device_info="not valid json {",
    )

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 1  # STATUS_FAILURE
    assert response.combine_result == ""


def test_optimal_combine_negative_invalid_qasm_in_job():
    topology = _make_linear_topology(10)
    # 'qbit' instead of 'qubit' => invalid QASM
    jobs = [{"job_id": "job-bad", "program": 'OPENQASM 3;\ninclude "stdgates.inc";\nqbit[2] q;bit[2] cbit;\nh q[0];'}]  # noqa: E501
    request = _make_optimal_combine_request(jobs, topology)

    response = CircuitCombiner().OptimalCombine(request, None)

    assert response.combined_status == 1  # STATUS_FAILURE
    assert response.combine_result == ""


# ===================================================================
# Tests for assign_environ
# ===================================================================


def test_assign_environ_expands_env_variable(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "/expanded/path")
    config = {"key": "$TEST_VAR"}
    result = assign_environ(config)
    assert result["key"] == "/expanded/path"


def test_assign_environ_expands_tilde():
    config = {"key": "~/some/path"}
    result = assign_environ(config)
    assert "~" not in result["key"]
    assert result["key"].endswith("/some/path")


def test_assign_environ_nested_dict(monkeypatch):
    monkeypatch.setenv("NESTED_VAR", "hello")
    config = {"outer": {"inner": "$NESTED_VAR"}}
    result = assign_environ(config)
    assert result["outer"]["inner"] == "hello"


def test_assign_environ_non_string_values():
    config = {"number": 42, "boolean": True, "none_val": None}
    result = assign_environ(config)
    assert result["number"] == 42
    assert result["boolean"] is True
    assert result["none_val"] is None


# ===================================================================
# Tests for InvalidQubitsError
# ===================================================================


def test_invalid_qubits_error_str():
    err = InvalidQubitsError(total_qubits=100, limit_qubits=64)
    assert "100" in str(err)
    assert "64" in str(err)
    assert "total qubits must be less than 64" in str(err)


def test_invalid_qubits_error_is_exception():
    err = InvalidQubitsError(total_qubits=10, limit_qubits=5)
    assert isinstance(err, Exception)


# ===================================================================
# Tests for _parse_args
# ===================================================================


def test_parse_args_defaults():
    with patch("sys.argv", ["app.py"]):
        args = _parse_args()
    assert args.config == "config/config.yaml"
    assert args.logging == "config/logging.yaml"


def test_parse_args_custom():
    with patch("sys.argv", ["app.py", "-c", "my_config.yaml", "-l", "my_log.yaml"]):
        args = _parse_args()
    assert args.config == "my_config.yaml"
    assert args.logging == "my_log.yaml"


# ===================================================================
# Tests for CustomTimedRotatingFileHandler
# ===================================================================


def test_custom_timed_rotating_file_handler_log_path(tmp_path):
    handler = CustomTimedRotatingFileHandler(log_dir=str(tmp_path))
    log_path = handler.log_path()
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    assert today in log_path
    assert str(tmp_path) in log_path
    assert log_path.endswith(".log")


def test_custom_timed_rotating_file_handler_custom_namer(tmp_path):
    handler = CustomTimedRotatingFileHandler(log_dir=str(tmp_path))
    # custom_namer should always return log_path() regardless of default_name
    result = handler.custom_namer("some_default_name")
    assert result == handler.log_path()


def test_custom_timed_rotating_file_handler_writes_log(tmp_path):
    import logging

    handler = CustomTimedRotatingFileHandler(log_dir=str(tmp_path))
    handler.setLevel(logging.DEBUG)
    test_logger = logging.getLogger("test_handler")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)
    test_logger.debug("test message")
    handler.close()
    test_logger.removeHandler(handler)
    log_file = Path(handler.log_path())
    assert log_file.exists()
    assert "test message" in log_file.read_text()


# ===================================================================
# Tests for serve
# ===================================================================


def test_serve_starts_and_configures_server(tmp_path):
    config = {"proto": {"max_workers": 2, "address": "[::]:59999"}}
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {},
        "root": {"level": "DEBUG", "handlers": []},
    }
    config_path = tmp_path / "config.yaml"
    logging_path = tmp_path / "logging.yaml"

    config_path.write_text(yaml.dump(config))
    logging_path.write_text(yaml.dump(logging_config))

    with patch("oqtopus_engine_combiner.app.grpc.server") as mock_grpc_server:
        mock_server = MagicMock()
        mock_grpc_server.return_value = mock_server
        serve(str(config_path), str(logging_path))
        mock_grpc_server.assert_called_once()
        mock_server.add_insecure_port.assert_called_once_with("[::]:59999")
        mock_server.start.assert_called_once()
        mock_server.wait_for_termination.assert_called_once()


def test_serve_uses_defaults_when_config_missing_values(tmp_path):
    config = {"proto": {}}
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {},
        "root": {"level": "DEBUG", "handlers": []},
    }
    config_path = tmp_path / "config.yaml"
    logging_path = tmp_path / "logging.yaml"

    config_path.write_text(yaml.dump(config))
    logging_path.write_text(yaml.dump(logging_config))

    with patch("oqtopus_engine_combiner.app.grpc.server") as mock_grpc_server:
        mock_server = MagicMock()
        mock_grpc_server.return_value = mock_server
        serve(str(config_path), str(logging_path))
        mock_server.add_insecure_port.assert_called_once_with("[::]:52013")
