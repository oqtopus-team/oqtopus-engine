import sys
from pathlib import Path

# add python path to import circuit_combiner.py
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.joinpath("src", "circuit_combiner"))
)

import logging
from unittest.mock import patch

import pytest
import qiskit.qasm3
from multiprog_interface.v1 import multiprog_pb2
from qiskit import QuantumCircuit

from circuit_combiner import (  # type: ignore[attr-defined]
    CircuitCombiner,
    get_allowed_threads,
    get_cgroup_cpu_count,
)


def test_combine_circuits_positive_1circuit():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

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
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [2]
    assert combined_qasm == comb_circ_text


def test_combine_circuits_positive_2circuits_clbits_qubits():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

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
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [1, 2]
    assert combined_qasm == comb_circ_text


def test_combine_circuits_positive_3circuits():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

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
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [1, 1, 2]
    assert combined_qasm == comb_circ_text


def test_combine_circuits_positive_3circuits_nochange_by_deviceinfo_in_maxcubits():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

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
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qubits_list == [1, 1, 2]
    assert combined_qasm == comb_circ_text


def test_combine_circuits_positive_2circuits_set_maxcubits_negative_value():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

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

    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 0
    assert combined_qasm == comb_circ_text
    assert combined_qubits_list == [0, 0]


def test_combine_circuits_negative_exceeded_maxcubits():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[63] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 2
    assert combined_qasm is None
    assert len(combined_qubits_list) == 0


def test_combine_circuits_negative_3circuits_exceeded_modified_maxcubits():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[59] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[3] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\ncx q[1], q[2];\nx q[2];\nmeasure q[2] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 63
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 2
    assert combined_qasm is None
    assert len(combined_qubits_list) == 0


def test_combine_circuits_negative_qubits_shortage():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[1] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 1
    assert combined_qasm is None
    assert len(combined_qubits_list) == 0


def test_combine_circuits_negative_qasm_string_error():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

    input_list = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqbit[2] q;bit[2] cbit;\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];\nmeasure q[1] -> cbit[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;bit[1] cbit;\n\nh q[0];\nx q[0];\ncx q[0], q[1];\nmeasure q[0] -> cbit[0];',  # noqa: E501
    ]
    max_qubits = 64
    status, combined_qasm, combined_qubits_list = cc.combine_circuits(
        input_list, max_qubits
    )
    assert status == 1
    assert combined_qasm is None
    assert len(combined_qubits_list) == 0


def test_deal_with_request_qasm_positive_qasm_str_conversion():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

    qasm_array_str = '[\\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqubit[2] q;\\n\\nh q[0];\\nx q[0];\\ncx q[0], q[1];\\", \\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqubit[2] q;\\n\\nh q[0];\\nx q[0];\\ncx q[0], q[1];\\"]'  # noqa: E501
    request = multiprog_pb2.CombineRequest(
        qasm_array=qasm_array_str,
        max_qubits=10,
    )
    json_array = [
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];',  # noqa: E501
        'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];',  # noqa: E501
    ]
    assert cc.deal_with_request_qasm(request) == json_array


def test_deal_with_request_qasm_negative_qasm_str_anomaly():
    logger = logging.getLogger(__name__)
    cc = CircuitCombiner(logger)

    qasm_array_str = """{"qasm": ["OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];", "OPENQASM 3;\ninclude "stdgates.inc";\nqubit[2] q;\n\nh q[0];\nx q[0];\ncx q[0], q[1];"]}"""  # noqa: E501
    request = multiprog_pb2.CombineRequest(
        qasm_array=qasm_array_str,
        max_qubits=10,
    )
    with pytest.raises(ValueError, match=r"Invalid input QASM array.*"):
        cc.deal_with_request_qasm(request)


def test_get_cgroup_cpu_count_quota_set_positive_cpu_num_obtainable_1st():
    with (
        patch("pathlib.Path.read_text", side_effect=["200000", "100000"]),
        patch("os.cpu_count", return_value=4),
    ):
        assert get_cgroup_cpu_count() == 2


def test_get_cgroup_cpu_count_quota_set_positive_cpu_num_obtainable_2nd():
    with (
        patch("pathlib.Path.read_text", side_effect=["400000", "100000"]),
        patch("os.cpu_count", return_value=4),
    ):
        assert get_cgroup_cpu_count() == 4


def test_get_cgroup_cpu_count_quota_set_positive_cpu_num_obtainable_host_os():
    with (
        patch("pathlib.Path.read_text", side_effect=["500000", "100000"]),
        patch("os.cpu_count", return_value=4),
    ):
        assert get_cgroup_cpu_count() == 4


def test_get_cgroup_cpu_count_quota_unlimited_positive_no_cpu_limit():
    with (
        patch("pathlib.Path.read_text", return_value="-1"),
        patch("os.cpu_count", return_value=7),
    ):
        assert get_cgroup_cpu_count() == 7


def test_get_cgroup_cpu_count_file_not_found():
    with (
        patch("builtins.open", side_effect=FileNotFoundError),
        patch("os.cpu_count", return_value=7),
    ):
        assert get_cgroup_cpu_count() == 7


@patch("circuit_combiner.MAX_WORKERS", 3)
def test_get_allowed_threads_positive_set_max_threads():
    with patch("circuit_combiner.get_cgroup_cpu_count", return_value=7):
        assert get_allowed_threads() == 3


def test_get_allowed_threads_positive_max_threads_unset():
    with patch("circuit_combiner.get_cgroup_cpu_count", return_value=7):
        assert get_allowed_threads() == 7


@patch("circuit_combiner.MAX_WORKERS", 0)
def test_get_allowed_threads_positive_max_threads_zero():
    with patch("circuit_combiner.get_cgroup_cpu_count", return_value=7):
        assert get_allowed_threads() == 7


@patch("circuit_combiner.MAX_WORKERS", -6)
def test_get_allowed_threads_positive_negative_max_threads():
    with patch("circuit_combiner.get_cgroup_cpu_count", return_value=7):
        assert get_allowed_threads() == 7


@patch("circuit_combiner.MAX_WORKERS", 8)
def test_get_allowed_threads_positive_exceeded_max_threads():
    with patch("circuit_combiner.get_cgroup_cpu_count", return_value=7):
        assert get_allowed_threads() == 7


def test_circuit_combiner_positive():
    logger = logging.getLogger(__name__)

    qasm_array_str = '[\\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqubit[2] q;\\n\\nh q[0];\\nx q[0];\\ncx q[0], q[1];\\", \\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqubit[1] q;\\n\\nh q[0];\\nx q[0];\\"]'  # noqa: E501
    request = multiprog_pb2.CombineRequest(
        qasm_array=qasm_array_str,
        max_qubits=10,
    )
    response = CircuitCombiner(logger).Combine(request, None)
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
    assert response.combined_qasm == comb_circ_text
    assert response.combined_qubits_list == [0, 0]


def test_circuit_combiner_negative_qasm_json_str_anomaly():
    logger = logging.getLogger(__name__)

    qasm_array_str = '"{\\"qasm\\": \\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqubit[2] q;\\n\\nh q[0];\\nx q[0];\\ncx q[0], q[1];\\", \\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqubit[1] q;\\n\\nh q[0];\\nx q[0];\\"]}"'  # noqa: E501
    request = multiprog_pb2.CombineRequest(
        qasm_array=qasm_array_str,
        max_qubits=10,
    )
    response = CircuitCombiner(logger).Combine(request, None)
    assert response.combined_status == 1
    assert response.combined_qasm == ""
    assert response.combined_qubits_list == []


def test_circuit_combiner_negative_qasm_circuit_str_anomaly():
    logger = logging.getLogger(__name__)

    qasm_array_str = '[\\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqbit[2] q;\\n\\nh q[0];\\nx q[0];\\ncx q[0], q[1];\\", \\"OPENQASM 3;\\ninclude \\\\"stdgates.inc\\\\";\\nqubit[1] q;\\n\\nh q[0];\\nx q[0];\\"]'  # noqa: E501
    request = multiprog_pb2.CombineRequest(
        qasm_array=qasm_array_str,
        max_qubits=10,
    )
    response = CircuitCombiner(logger).Combine(request, None)
    assert response.combined_status == 1
    assert response.combined_qasm == ""
    assert response.combined_qubits_list == []
