import json
from unittest.mock import patch

import pytest
from qiskit import QuantumCircuit  # type: ignore[import-untyped]

from oqtopus_engine_core.interfaces.mitigator_interface.v1.mitigator_pb2 import (  # type: ignore[attr-defined]
    ReqQcxtPostProcessRequest,
    ReqQcxtPreProcessRequest,
)
from oqtopus_engine_mitigator.qcxt.device import DeviceData
from oqtopus_engine_mitigator.qcxt.service import Qcxt, _cut
from oqtopus_engine_mitigator.qcxt.simulator import Simulator
from oqtopus_engine_mitigator.qcxt.solver import SelectedCut

BELL_QASM = (
    'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n'
)
PHYSICAL_BELL_QASM = (
    'OPENQASM 3.0;\ninclude "stdgates.inc";\nbit[2] c;\n'
    "h $9;\ncx $9, $11;\nc[0] = measure $9;\nc[1] = measure $11;\n"
)
BASIS_GATES = ["cx", "id", "rz", "sx", "x"]


@pytest.fixture(scope="session")
def qcxt() -> Qcxt:
    return Qcxt(max_cuts=1, fidelity_threshold=0.96, min_partition_ratio=0.0)


def make_device_info(*, coupling_fidelity: float) -> dict:
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
        ],
        "couplings": [
            {"control": 0, "target": 1, "fidelity": coupling_fidelity},
        ],
    }


def make_device_data(*, coupling_fidelity: float) -> DeviceData:
    return DeviceData(
        one_qubit_fidelities={0: 0.999, 1: 0.998},
        two_qubit_fidelities={(0, 1): coupling_fidelity},
        readout_errors={0: 0.01, 1: 0.02},
    )


def test_qcxt_treats_negative_max_cuts_as_zero():
    # Test that a negative max_cuts is replaced by 0 so that no cuts are made.
    qcxt = Qcxt(max_cuts=-1, fidelity_threshold=0.96, min_partition_ratio=0.0)

    assert qcxt.max_cuts == 0


def test_reqqcxt_preprocess_returns_response(qcxt: Qcxt):
    # Test that the ReqQcxtPreProcess method returns a valid response with expected sub-programs and coefficients.
    request = ReqQcxtPreProcessRequest(
        program=BELL_QASM,
        device_info=json.dumps(make_device_info(coupling_fidelity=0.99)),
        shots=1000,
        basis_gates=BASIS_GATES,
    )

    response = qcxt.ReqQcxtPreProcess(request, None)

    assert response is not None
    assert list(response.sub_programs) == [BELL_QASM]
    assert list(response.coefficients) == pytest.approx([1.0])
    assert list(response.virtual_couplings) == []


def test_reqqcxt_postprocess_returns_response(qcxt: Qcxt):
    # Test that the ReqQcxtPostProcess method returns a valid response with expected exp_val and stds.
    request = ReqQcxtPostProcessRequest(
        sub_expvals=[0.1, 0.9, -0.1, -0.9],
        sub_stds=[0.01, -0.01, -0.02, 0.02],
        coefficients=[0.5, 0.5, 0.5, -0.5],
    )

    response = qcxt.ReqQcxtPostProcess(request, None)

    assert response is not None
    assert response.exp_val == pytest.approx(
        0.5 * 0.1 + 0.5 * 0.9 + 0.5 * -0.1 + -0.5 * -0.9, abs=1e-6
    )
    assert response.stds == pytest.approx(
        0.5 * 0.01 + 0.5 * -0.01 + 0.5 * -0.02 + 0.5 * 0.02, abs=1e-6
    )


def test_preprocess_falls_back_when_judge_false(qcxt: Qcxt):
    # Test that the _preprocess method falls back when the judge method returns False.
    device_data = make_device_data(coupling_fidelity=0.90)

    with patch.object(Simulator, "judge", return_value=False):
        sub_programs, coefficients, selected_cuts = qcxt._preprocess(
            BELL_QASM, device_data, 1000, BASIS_GATES
        )

    assert len(sub_programs) == 1
    assert coefficients == [1.0]
    assert selected_cuts == []


def test_preprocess_applies_cut_when_judge_true(qcxt: Qcxt):
    # Test that the _preprocess method applies a cut when the judge method returns True.
    device_data = make_device_data(coupling_fidelity=0.90)

    with patch.object(Simulator, "judge", return_value=True):
        sub_programs, coefficients, selected_cuts = qcxt._preprocess(
            BELL_QASM, device_data, 1000, BASIS_GATES
        )

    assert (
        len(sub_programs) == 4
    )  # max_cuts=1 forces 1 cut, resulting in 4 sub-programs
    assert len(coefficients) == 4
    assert len(selected_cuts) == 1
    cut = selected_cuts[0]
    assert cut["control"] == 0
    assert cut["target"] == 1
    assert cut["fidelity"] == pytest.approx(0.90)
    assert cut["cx_index"] == 0


def make_physical_device_data(*, coupling_fidelity: float) -> DeviceData:
    return DeviceData(
        one_qubit_fidelities={9: 0.999, 11: 0.998},
        two_qubit_fidelities={(9, 11): coupling_fidelity},
        readout_errors={9: 0.01, 11: 0.02},
    )


def test_preprocess_keeps_physical_qubit_notation(qcxt: Qcxt):
    # Test that the exported sub-programs from _preprocess keep the physical qubit notation (e.g., $9, $11).
    device_data = make_physical_device_data(coupling_fidelity=0.90)

    with patch.object(Simulator, "judge", return_value=True):
        sub_programs, _coefficients, selected_cuts = qcxt._preprocess(
            PHYSICAL_BELL_QASM, device_data, 1000, BASIS_GATES
        )

    assert len(selected_cuts) == 1
    assert selected_cuts[0]["control"] == 9
    assert selected_cuts[0]["target"] == 11
    assert selected_cuts[0]["cx_index"] == 0
    for program in sub_programs:
        assert "q[" not in program
        assert "qubit[" not in program
        assert "$9" in program
        assert "$11" in program


def test_cut_single_cx_with_z_and_measure():
    # Test that the _cut function cuts a single CX gate in a circuit.
    # Circuit with a Z gate, a CX (to be cut) and measurements on 2 qubits.
    qc = QuantumCircuit(2, 2)
    qc.z(0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    basis_gates = ["x", "sx", "cx", "rz"]
    cut_pairs = [
        SelectedCut(
            control=0,
            target=1,
            edge_key=0,
            fidelity=0.90,
            instruction_index=1,
        )
    ]

    sub_circuits, coefficients, cut_cx_indices = _cut(qc, cut_pairs, basis_gates)

    assert len(sub_circuits) == 4
    assert coefficients == pytest.approx([0.5, 0.5, 0.5, -0.5])
    assert cut_cx_indices == [0]

    replacement_allowed_ops = {"x", "sx", "rz"}
    replacement_gate_totals = []
    for sub_qc in sub_circuits:
        assert sub_qc.num_qubits == 2
        assert sub_qc.num_clbits == 2
        ops = sub_qc.count_ops()
        # The only CX was cut and replaced by single-qubit gates.
        assert "cx" not in ops
        # Non-cut operations must remain unchanged.
        assert ops.get("z", 0) == 1
        assert ops.get("measure", 0) == 2

        replacement_ops = {
            name: count for name, count in ops.items() if name not in {"z", "measure"}
        }
        assert set(replacement_ops).issubset(replacement_allowed_ops)
        replacement_gate_totals.append(sum(replacement_ops.values()))

    # The (I, I) decomposition term introduces no replacement gates.
    assert any(total == 0 for total in replacement_gate_totals)
