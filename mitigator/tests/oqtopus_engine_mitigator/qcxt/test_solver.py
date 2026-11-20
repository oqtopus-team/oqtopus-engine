from unittest.mock import MagicMock, patch

import pytest
from qiskit import QuantumCircuit  # type: ignore[import-untyped]

from oqtopus_engine_mitigator.qcxt.device import DeviceData
from oqtopus_engine_mitigator.qcxt.solver import CutSelector


def make_device(*, two_qubit_fidelities: dict[tuple[int, int], float]) -> DeviceData:
    qubit_ids = sorted({q for pair in two_qubit_fidelities for q in pair})
    return DeviceData(
        one_qubit_fidelities=dict.fromkeys(qubit_ids, 0.999),
        two_qubit_fidelities=two_qubit_fidelities,
        readout_errors=dict.fromkeys(qubit_ids, 0.01),
    )


def test_circuit_to_graph_only_cx_becomes_edges():
    # Test that the circuit_to_graph method converts a circuit into a NetworkX graph.
    device = make_device(two_qubit_fidelities={(0, 1): 0.98, (1, 2): 0.97})
    selector = CutSelector(
        device, max_cuts=3, cut_fidelity_threshold=0.96, min_partition_ratio=0.0
    )
    qc = QuantumCircuit(3)
    qc.h(0)
    qc.x(1)
    qc.cx(0, 1)
    qc.cx(1, 2)

    graph = selector.circuit_to_graph(qc)

    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2
    edges = {
        (control, target): attrs
        for control, target, _key, attrs in graph.edges(keys=True, data=True)
    }
    # Only cx gates become edges; h and x are skipped.
    assert set(edges) == {(0, 1), (1, 2)}
    assert edges[0, 1]["fidelity"] == pytest.approx(0.98)
    assert edges[1, 2]["fidelity"] == pytest.approx(0.97)
    # h and x are skipped; the two cx gates are at index 2 and 3 in circuit.data.
    assert edges[0, 1]["instruction_index"] == 2
    assert edges[1, 2]["instruction_index"] == 3


def test_solve_no_cut_when_all_fidelity_high():
    # Test that the solve method returns no cuts when all fidelities are above the threshold.
    device = make_device(two_qubit_fidelities={(0, 1): 0.99, (1, 2): 0.98})
    selector = CutSelector(
        device, max_cuts=3, cut_fidelity_threshold=0.96, min_partition_ratio=0.0
    )
    qc = QuantumCircuit(3)
    qc.cx(0, 1)
    qc.cx(1, 2)

    cuts = selector.solve(selector.circuit_to_graph(qc))

    assert cuts == []


def test_solve_truncates_low_fidelity_cuts_to_max_cuts():
    # Test that the solve method truncates the selected cuts to respect the max_cuts parameter.
    device = make_device(
        two_qubit_fidelities={(0, 1): 0.90, (1, 2): 0.85, (0, 2): 0.80},
    )
    selector = CutSelector(
        device, max_cuts=2, cut_fidelity_threshold=0.96, min_partition_ratio=0.0
    )
    qc = QuantumCircuit(3)
    qc.cx(0, 1)
    qc.cx(1, 2)
    qc.cx(0, 2)

    cuts = selector.solve(selector.circuit_to_graph(qc))

    # Only max_cuts (=2) cuts remain.
    assert len(cuts) == 2
    selected_fidelities = sorted(cut.fidelity for cut in cuts)
    assert selected_fidelities == pytest.approx([0.80, 0.85])
    # The highest-fidelity low edge (0.90) is dropped by the count truncation.
    assert all(cut.fidelity != pytest.approx(0.90) for cut in cuts)


def test_solve_falls_back_when_milp_fails():
    # Test that the solve method falls back to a heuristic when the MILP solver fails.
    device = make_device(
        two_qubit_fidelities={(0, 1): 0.96, (1, 2): 0.80, (0, 2): 0.99},
    )
    selector = CutSelector(
        device, max_cuts=2, cut_fidelity_threshold=0.96, min_partition_ratio=0.0
    )
    qc = QuantumCircuit(3)
    qc.cx(0, 1)
    qc.cx(1, 2)
    qc.cx(0, 2)
    graph = selector.circuit_to_graph(qc)

    failed_result = MagicMock(success=False, message="mip infeasible")
    with patch("oqtopus_engine_mitigator.qcxt.solver.milp", return_value=failed_result):
        cuts = selector.solve(graph)

    # Fallback selects lowest-fidelity edges up to max_cuts; both edges are cut.
    assert len(cuts) == 2
    assert sorted((cut.control, cut.target) for cut in cuts) == [(0, 1), (1, 2)]
    assert sorted(cut.fidelity for cut in cuts) == pytest.approx([0.80, 0.96])
