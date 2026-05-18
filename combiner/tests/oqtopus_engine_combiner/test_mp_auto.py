import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent.joinpath("src")))

import networkx as nx  # type: ignore[import-untyped]
from qiskit import QuantumCircuit  # type: ignore[import-untyped]

from oqtopus_engine_combiner.mp_auto import (  # type: ignore[import-untyped]
    JobWithCircuitGraph,
    OptimalCircuitCombiner,
)


# --- Helper fixtures and data ---

SIMPLE_1Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[1] q;\n"
    "bit[1] cbit;\n"
    "h q[0];\n"
    "measure q[0] -> cbit[0];"
)

SIMPLE_2Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[2] q;\n"
    "bit[2] cbit;\n"
    "h q[0];\n"
    "x q[0];\n"
    "cx q[0], q[1];\n"
    "measure q[0] -> cbit[0];\n"
    "measure q[1] -> cbit[1];"
)

SIMPLE_3Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[3] q;\n"
    "bit[3] cbit;\n"
    "h q[0];\n"
    "cx q[0], q[1];\n"
    "cx q[2], q[1];\n"
    "measure q[0] -> cbit[0];\n"
    "measure q[1] -> cbit[1];\n"
    "measure q[2] -> cbit[2];\n"
)

UNASSIGNABLE_3Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[3] q;\n"
    "bit[3] cbit;\n"
    "h q[0];\n"
    "cx q[0], q[1];\n"
    "cx q[1], q[2];\n"
    "measure q[0] -> cbit[0];\n"
    "measure q[1] -> cbit[1];\n"
    "measure q[2] -> cbit[2];\n"
)


def make_linear_topology(n_qubits: int) -> dict[str, Any]:
    """Create a linear topology with n_qubits connected in a chain.

    Each qubit is exclusively a control (C) or target (T) node.
    Even-index nodes are controls, odd-index nodes are targets.

    Example for n_qubits=5::

        C    T    C    T    C
        0 →  1 ←  2 →  3 ←  4

    """
    qubits = [{"id": i, "position": {"x": i, "y": 0}} for i in range(n_qubits)]
    couplings = []
    for i in range(n_qubits - 1):
        if i % 2 == 0:
            couplings.append({"control": i, "target": i + 1})
        else:
            couplings.append({"control": i + 1, "target": i})
    return {"qubits": qubits, "couplings": couplings}


def make_grid_topology(rows: int, cols: int) -> dict[str, Any]:
    """Create a grid topology with rows x cols qubits connected to right/bottom neighbors.

    Each qubit is exclusively a control (C) or target (T) node, assigned
    by checkerboard parity: (row + col) even → control, odd → target.

    Example for 3x4 (rows=3, cols=4)::

         0 →  1 ←  2 →  3
         ↓    ↑    ↓    ↑
         4 ←  5 →  6 ←  7
         ↑    ↓    ↑    ↓
         8 →  9 ← 10 → 11

    """
    qubits = []
    for r in range(rows):
        for c in range(cols):
            node_id = r * cols + c
            qubits.append({"id": node_id, "position": {"x": c, "y": r}})

    couplings = []
    for r in range(rows):
        for c in range(cols):
            node_id = r * cols + c
            # Horizontal edge to right neighbor
            if c + 1 < cols:
                right = node_id + 1
                if (r + c) % 2 == 0:
                    couplings.append({"control": node_id, "target": right})
                else:
                    couplings.append({"control": right, "target": node_id})
            # Vertical edge to bottom neighbor
            if r + 1 < rows:
                below = node_id + cols
                if (r + c) % 2 == 0:
                    couplings.append({"control": node_id, "target": below})
                else:
                    couplings.append({"control": below, "target": node_id})

    return {"qubits": qubits, "couplings": couplings}


def make_grid_topology_with_defects() -> dict[str, Any]:
    """Create an 8x8 grid topology with 2 missing edges to simulate defective connections.

    Uses :func:`make_grid_topology` then removes two edges:
      - (27, 19): edge between node 27 (control) and node 19 (target)
      - (36, 37): edge between node 36 (control) and node 37 (target)

    Layout::

         0 →  1 ←  2 →  3 ←  4 →  5 ←  6 →  7
         ↓    ↑    ↓    ↑    ↓    ↑    ↓    ↑
         8 ←  9 → 10 ← 11 → 12 ← 13 → 14 ← 15
         ↑    ↓    ↑    ↓    ↑    ↓    ↑    ↓
        16 → 17 ← 18 → 19 ← 20 → 21 ← 22 → 23
         ↓    ↑    ↓         ↓    ↑    ↓    ↑
        24 ← 25 → 26 ← 27 → 28 ← 29 → 30 ← 31
         ↑    ↓    ↑    ↓    ↑    ↓    ↑    ↓
        32 → 33 ← 34 → 35 ← 36   37 ← 38 → 39
         ↓    ↑    ↓    ↑    ↓    ↑    ↓    ↑
        40 ← 41 → 42 ← 43 → 44 ← 45 → 46 ← 47
         ↑    ↓    ↑    ↓    ↑    ↓    ↑    ↓
        48 → 49 ← 50 → 51 ← 52 → 53 ← 54 → 55
         ↓    ↑    ↓    ↑    ↓    ↑    ↓    ↑
        56 ← 57 → 58 ← 59 → 60 ← 61 → 62 ← 63

    """
    topo = make_grid_topology(8, 8)
    defect_edges = {(27, 19), (36, 37)}
    topo["couplings"] = [
        c for c in topo["couplings"]
        if (c["control"], c["target"]) not in defect_edges
    ]
    return topo


# ===================================================================
# Tests for JobWithCircuitGraph
# ===================================================================


class TestJobWithCircuitGraph:
    """Tests for the JobWithCircuitGraph class."""

    def test_init_creates_circuit_graph(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        assert job.job_id == "job-1"
        assert job.program == SIMPLE_2Q_QASM
        assert isinstance(job.circuit_graph, nx.MultiDiGraph)
        assert job.original_to_graph_mapping is not None
        assert job.graph_matching_mapping is None
        assert job.original_matching_mapping is None

    def test_circuit_graph_nodes_count_2qubit(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        # 2-qubit circuit with cx should have 2 nodes and 1 edge between them
        assert job.circuit_graph.number_of_nodes() == 2
        assert job.circuit_graph.number_of_edges() >= 1

    def test_circuit_graph_1qubit(self):
        job = JobWithCircuitGraph(job_id="job-1q", program=SIMPLE_1Q_QASM)
        # single qubit circuit has 1 node and no edges (no multi-qubit gates)
        assert job.circuit_graph.number_of_nodes() == 1
        assert job.circuit_graph.number_of_edges() == 0

    def test_circuit_graph_3qubit(self):
        job = JobWithCircuitGraph(job_id="job-3q", program=SIMPLE_3Q_QASM)
        # 3 qubits with cx(0,1) and cx(2,1) => 3 nodes, 2 edges
        assert job.circuit_graph.number_of_nodes() == 3
        assert job.circuit_graph.number_of_edges() == 2

    def test_original_to_graph_mapping_keys(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        # mapping should contain keys for the original qubit indices
        assert set(job.original_to_graph_mapping.keys()) == {0, 1}

    def test_update_original_matching_mapping(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        # Manually set graph_matching_mapping
        job.graph_matching_mapping = {0: 5, 1: 10}
        job.update_original_matching_mapping()

        assert job.original_matching_mapping is not None
        # original qubit 0 -> graph node X -> physical qubit from graph_matching_mapping
        for orig_q, graph_node in job.original_to_graph_mapping.items():
            assert job.original_matching_mapping[orig_q] == job.graph_matching_mapping[graph_node]

    def test_update_original_matching_mapping_none_when_graph_mapping_is_none(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        # graph_matching_mapping is None by default
        job.update_original_matching_mapping()
        assert job.original_matching_mapping is None

    def test_to_dict(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        job.graph_matching_mapping = {0: 5, 1: 10}
        job.update_original_matching_mapping()

        d = job.to_dict()
        assert d["job_id"] == "job-1"
        assert d["program"] == SIMPLE_2Q_QASM
        assert d["qubit_mapping"] == job.original_matching_mapping

    def test_to_dict_qubit_mapping_none(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        d = job.to_dict()
        assert d["qubit_mapping"] is None

    def test_quantum_circuit_to_networkx_unused_qubits_ignored(self):
        """Unused qubits should not appear as nodes in the graph."""
        qasm = (
            'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[5] q;bit[1] cbit;\n'
            "h q[0];\ncx q[0], q[4];\nmeasure q[0] -> cbit[0];"
        )
        job = JobWithCircuitGraph(job_id="job-sparse", program=qasm)
        # Only qubits 0 and 4 are used, so graph should have 2 nodes
        assert job.circuit_graph.number_of_nodes() == 2
        # cx creates 1 edge
        assert job.circuit_graph.number_of_edges() == 1


# ===================================================================
# Tests for OptimalCircuitCombiner
# ===================================================================


class TestOptimalCircuitCombiner:
    """Tests for the OptimalCircuitCombiner class."""

    # --- create_topology_graph ---

    def test_create_topology_graph_linear(self):
        topology_json = make_linear_topology(5)
        g = OptimalCircuitCombiner.create_topology_graph(topology_json)
        assert isinstance(g, nx.MultiDiGraph)
        assert g.number_of_nodes() == 5
        assert g.number_of_edges() == 4

    def test_create_topology_graph_grid_with_defects(self):
        topology_json = make_grid_topology_with_defects()
        g = OptimalCircuitCombiner.create_topology_graph(topology_json)
        assert isinstance(g, nx.MultiDiGraph)
        assert g.number_of_nodes() == 64
        # 8x8 grid has 7*8 + 8*7 = 112 edges, minus 2 defects = 110
        assert g.number_of_edges() == 110

    def test_create_topology_graph_single_qubit(self):
        topology_json = {"qubits": [{"id": 0, "position": {"x": 0, "y": 0}}], "couplings": []}
        g = OptimalCircuitCombiner.create_topology_graph(topology_json)
        assert g.number_of_nodes() == 1
        assert g.number_of_edges() == 0

    # --- assign_circuits ---

    def test_assign_circuits_single_job(self):
        combiner = OptimalCircuitCombiner()
        topology = make_grid_topology_with_defects()
        jobs = [{"job_id": "job-1", "program": SIMPLE_2Q_QASM}]

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)

        assert "job-1" in assigned_ids
        assert len(assigned_groups) == 1
        assert len(assigned_groups[0]) == 1
        assert assigned_groups[0][0].job_id == "job-1"
        # original_matching_mapping should be set
        assert assigned_groups[0][0].original_matching_mapping is not None

    def test_assign_circuits_multiple_jobs_fit(self):
        combiner = OptimalCircuitCombiner()
        # Large enough topology to fit two 2-qubit circuits
        topology = make_grid_topology_with_defects()
        jobs = [
            {"job_id": "job-1", "program": SIMPLE_1Q_QASM},
            {"job_id": "job-2", "program": SIMPLE_2Q_QASM},
            {"job_id": "job-3", "program": SIMPLE_3Q_QASM},
        ]

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)

        assert "job-1" in assigned_ids
        assert "job-2" in assigned_ids
        assert "job-3" in assigned_ids

    def test_assign_circuits_job_too_large_for_topology(self):
        combiner = OptimalCircuitCombiner()
        # Topology with only 1 qubit, circuit needs 2
        topology = {"qubits": [{"id": 0, "position": {"x": 0, "y": 0}}], "couplings": []}
        jobs = [{"job_id": "job-1", "program": SIMPLE_2Q_QASM}]

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)

        assert "job-1" not in assigned_ids
        assert len(assigned_groups) == 0

    def test_assign_circuits_1qubit_job_on_single_qubit_topology(self):
        combiner = OptimalCircuitCombiner()
        topology = {"qubits": [{"id": 0, "position": {"x": 0, "y": 0}}], "couplings": []}
        jobs = [{"job_id": "job-1q", "program": SIMPLE_1Q_QASM}]

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)

        assert "job-1q" in assigned_ids
        assert len(assigned_groups) == 1

    def test_assign_circuits_empty_jobs(self):
        combiner = OptimalCircuitCombiner()
        topology = make_linear_topology(5)
        jobs = []

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)

        assert assigned_ids == []
        assert assigned_groups == []

    def test_assign_circuits_mappings_are_nonoverlapping(self):
        combiner = OptimalCircuitCombiner()
        topology = make_linear_topology(10)
        jobs = [
            {"job_id": "job-1", "program": SIMPLE_2Q_QASM},
            {"job_id": "job-2", "program": SIMPLE_2Q_QASM},
        ]

        _, assigned_groups = combiner.assign_circuits(jobs, topology)

        # All physical qubits assigned should be non-overlapping within a group
        for group in assigned_groups:
            all_physical_qubits = []
            for job in group:
                all_physical_qubits.extend(job.original_matching_mapping.values())
            assert len(all_physical_qubits) == len(set(all_physical_qubits))

    def test_assign_circuits_including_unassignable_job(self):
        combiner = OptimalCircuitCombiner()
        topology = make_grid_topology(3, 3)  # 9 qubits in a grid
        jobs = [
            {"job_id": "job-1", "program": UNASSIGNABLE_3Q_QASM},  # Unable to fit due to linear chain of CX gates
            {"job_id": "job-2", "program": SIMPLE_1Q_QASM},  # Should be assignable
        ]

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)

        assert "job-1" not in assigned_ids  # Unassignable job should not be assigned
        assert "job-2" in assigned_ids  # The 1Q job should be assigned
        assert len(assigned_groups) == 1    # Only one group with the assignable job

    # --- combine_circuits ---

    def test_combine_circuits_single_job(self):
        combiner = OptimalCircuitCombiner()
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        job.graph_matching_mapping = {
            v: v for v in range(job.circuit_graph.number_of_nodes())
        }
        job.update_original_matching_mapping()

        combined_qasm, combined_qubits_list, n_total_qubits = \
            combiner.combine_circuits([job])

        assert isinstance(combined_qasm, str)
        assert "OPENQASM" in combined_qasm
        assert combined_qubits_list == [2]  # 2 classical bits
        assert n_total_qubits >= 2

    def test_combine_circuits_two_jobs(self):
        combiner = OptimalCircuitCombiner()

        job1 = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        job1.graph_matching_mapping = {0: 0, 1: 1}
        job1.update_original_matching_mapping()

        job2 = JobWithCircuitGraph(job_id="job-2", program=SIMPLE_1Q_QASM)
        job2.graph_matching_mapping = {0: 2}
        job2.update_original_matching_mapping()

        combined_qasm, combined_qubits_list, n_total_qubits = \
            combiner.combine_circuits([job1, job2])

        assert isinstance(combined_qasm, str)
        assert combined_qubits_list == [2, 1]  # job1: 2 clbits, job2: 1 clbit
        assert n_total_qubits == 3

    def test_combine_circuits_preserves_gate_operations(self):
        combiner = OptimalCircuitCombiner()
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        job.graph_matching_mapping = {0: 0, 1: 1}
        job.update_original_matching_mapping()

        combined_qasm, _, _ = combiner.combine_circuits([job])

        # The combined circuit should contain the gate operations
        assert "h" in combined_qasm
        assert "x" in combined_qasm
        assert "cx" in combined_qasm

    # --- combine_circuits_for_groups ---

    def test_combine_circuits_for_groups_single_group(self):
        combiner = OptimalCircuitCombiner()

        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        job.graph_matching_mapping = {0: 0, 1: 1}
        job.update_original_matching_mapping()

        result = combiner.combine_circuits_for_groups([[job]])

        assert len(result) == 1
        assert "combined_program" in result[0]
        assert "combine_info" in result[0]
        info = result[0]["combine_info"]
        assert info["assigned_ids"] == ["job-1"]
        assert info["combined_qubits_list"] == [2]
        assert info["n_total_qubits"] >= 2

    def test_combine_circuits_for_groups_multiple_groups(self):
        combiner = OptimalCircuitCombiner()

        job1 = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        job1.graph_matching_mapping = {0: 0, 1: 1}
        job1.update_original_matching_mapping()

        job2 = JobWithCircuitGraph(job_id="job-2", program=SIMPLE_1Q_QASM)
        job2.graph_matching_mapping = {0: 0}
        job2.update_original_matching_mapping()

        result = combiner.combine_circuits_for_groups([[job1], [job2]])

        assert len(result) == 2
        assert result[0]["combine_info"]["assigned_ids"] == ["job-1"]
        assert result[1]["combine_info"]["assigned_ids"] == ["job-2"]

    def test_combine_circuits_for_groups_assigned_group_has_to_dict(self):
        combiner = OptimalCircuitCombiner()

        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)
        job.graph_matching_mapping = {0: 0, 1: 1}
        job.update_original_matching_mapping()

        result = combiner.combine_circuits_for_groups([[job]])

        assigned_group = result[0]["combine_info"]["assigned_group"]
        assert len(assigned_group) == 1
        assert assigned_group[0]["job_id"] == "job-1"
        assert assigned_group[0]["qubit_mapping"] is not None

    # --- _copy_gates_with_mapping ---

    def test_copy_gates_with_mapping_identity(self):
        source = QuantumCircuit(2, 2)
        source.h(0)
        source.cx(0, 1)
        source.measure(0, 0)
        source.measure(1, 1)

        target = QuantumCircuit(2, 2)
        mapping = {0: 0, 1: 1}

        result_circuit, measure_ops = OptimalCircuitCombiner._copy_gates_with_mapping(
            source, target, mapping
        )

        # Non-measurement gates should be copied
        gate_names = [instr.operation.name for instr in result_circuit.data]
        assert "h" in gate_names
        assert "cx" in gate_names
        # Measurements should be collected separately
        assert len(measure_ops) == 2

    def test_copy_gates_with_mapping_remapped(self):
        source = QuantumCircuit(2, 2)
        source.h(0)
        source.cx(0, 1)
        source.measure(0, 0)
        source.measure(1, 1)

        target = QuantumCircuit(4, 2)
        mapping = {0: 2, 1: 3}

        result_circuit, measure_ops = OptimalCircuitCombiner._copy_gates_with_mapping(
            source, target, mapping
        )

        # Gates should be on remapped qubits (indices 2,3 in target)
        for instr in result_circuit.data:
            for q in instr.qubits:
                idx = result_circuit.find_bit(q).index
                assert idx in {2, 3}

        assert len(measure_ops) == 2

    # --- _find_nonoverlapping_subgraphs_with_t_nodes ---

    def test_find_nonoverlapping_single_match(self):
        topology = OptimalCircuitCombiner.create_topology_graph(make_linear_topology(5))
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        results = OptimalCircuitCombiner._find_nonoverlapping_subgraphs_with_t_nodes(
            topology, [job]
        )

        assert len(results) == 1
        assert results[0]["job_id"] == "job-1"
        assert results[0]["G_index"] == 0
        assert len(results[0]["mapping"]) == job.circuit_graph.number_of_nodes()
        # Mapped T_nodes should be valid topology node IDs
        for node in results[0]["T_nodes"]:
            assert node in topology.nodes()

    def test_find_nonoverlapping_multiple_matches_non_overlapping(self):
        topology = OptimalCircuitCombiner.create_topology_graph(
            make_grid_topology_with_defects()
        )
        jobs = [
            JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM),
            JobWithCircuitGraph(job_id="job-2", program=SIMPLE_1Q_QASM),
            JobWithCircuitGraph(job_id="job-3", program=SIMPLE_3Q_QASM),
            JobWithCircuitGraph(job_id="job-4", program=SIMPLE_2Q_QASM),
            JobWithCircuitGraph(job_id="job-5", program=SIMPLE_1Q_QASM),
        ]

        results = OptimalCircuitCombiner._find_nonoverlapping_subgraphs_with_t_nodes(
            topology, jobs
        )

        assert len(results) == 5
        t_edges = set(topology.edges())
        for r in results:
            # the number of qubits in each job should match the number of T_nodes assigned
            job = next(j for j in jobs if j.job_id == r["job_id"])
            assert len(r["T_nodes"]) == job.circuit_graph.number_of_nodes()

            # For each edge in circuit graph, mapped nodes must have an edge in topology
            mapping = r["mapping"]
            for u, v in job.circuit_graph.edges():
                assert (mapping[int(u)], mapping[int(v)]) in t_edges

        all_t_nodes: list[set[int]] = [set(r["T_nodes"]) for r in results]
        for i in range(len(all_t_nodes)):
            assert all_t_nodes[i].issubset(topology.nodes())  # sanity check: T_nodes should be valid topology nodes
            # T_nodes should not overlap across any pair of matches
            for j in range(i + 1, len(all_t_nodes)):
                assert all_t_nodes[i].isdisjoint(all_t_nodes[j])

    def test_find_nonoverlapping_partial_match_on_small_topology(self):
        """On a 2x2 topology (4 nodes), a 3Q job consumes 3 nodes so a 2Q job can't fit.

        Layout::

            0 →  1
            ↓    ↑
            2 ←  3

        job-1 (3Q star: 0→1←2) maps to 3 of 4 nodes, leaving only 1 free node.
        job-2 (2Q) needs 2 connected nodes but only 1 remains → unmatched.
        job-3 (1Q) needs just 1 node → matched on the remaining node.
        """
        topology_json = make_grid_topology(2, 2)
        topology = OptimalCircuitCombiner.create_topology_graph(topology_json)

        jobs = [
            JobWithCircuitGraph(job_id="job-1", program=SIMPLE_3Q_QASM),
            JobWithCircuitGraph(job_id="job-2", program=SIMPLE_2Q_QASM),
            JobWithCircuitGraph(job_id="job-3", program=SIMPLE_1Q_QASM),
        ]

        results = OptimalCircuitCombiner._find_nonoverlapping_subgraphs_with_t_nodes(
            topology, jobs
        )

        matched_ids = {r["job_id"] for r in results}
        # job-1 (3Q) and job-3 (1Q) should match; job-2 (2Q) should not
        assert len(results) == 2
        assert "job-1" in matched_ids
        assert "job-2" not in matched_ids
        assert "job-3" in matched_ids

        t_edges = set(topology.edges())
        for r in results:
            # the number of qubits in each job should match the number of T_nodes assigned
            job = next(j for j in jobs if j.job_id == r["job_id"])
            assert len(r["T_nodes"]) == job.circuit_graph.number_of_nodes()

            # For each edge in circuit graph, mapped nodes must have an edge in topology
            mapping = r["mapping"]
            for u, v in job.circuit_graph.edges():
                assert (mapping[int(u)], mapping[int(v)]) in t_edges

        # Matched T_nodes must not overlap
        all_t_nodes = [set(r["T_nodes"]) for r in results]
        for i in range(len(all_t_nodes)):
            for j in range(i + 1, len(all_t_nodes)):
                assert all_t_nodes[i].isdisjoint(all_t_nodes[j])

    def test_find_nonoverlapping_no_match(self):
        # Topology with 1 node, circuit needs 2 connected qubits
        topology = OptimalCircuitCombiner.create_topology_graph(
            {"qubits": [{"id": 0, "position": {"x": 0, "y": 0}}], "couplings": []}
        )
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        results = OptimalCircuitCombiner._find_nonoverlapping_subgraphs_with_t_nodes(
            topology, [job]
        )

        assert len(results) == 0

    # --- _draw_graph (smoke test) ---

    def test_draw_graph_does_not_raise(self, tmp_path):
        topology_json = make_linear_topology(5)
        topology = OptimalCircuitCombiner.create_topology_graph(topology_json)
        matches = [{"T_nodes": [0, 1]}]
        filename = str(tmp_path / "test_graph.png")

        # Should not raise
        OptimalCircuitCombiner._draw_graph(topology, topology_json, matches, filename)

    # --- End-to-end: assign + combine ---

    def test_end_to_end_assign_and_combine(self):
        combiner = OptimalCircuitCombiner()
        topology = make_grid_topology(3,3)
        jobs = [
            {"job_id": "job-1", "program": SIMPLE_2Q_QASM},
            {"job_id": "job-2", "program": SIMPLE_1Q_QASM},
            {"job_id": "job-3", "program": SIMPLE_3Q_QASM},
        ]

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)
        combined_groups = combiner.combine_circuits_for_groups(assigned_groups)

        # Both jobs should be assigned
        assert set(assigned_ids) == {"job-1", "job-2", "job-3"}
        # All jobs fit in a single group
        assert len(assigned_groups) == 1
        assert len(combined_groups) == 1

        cg = combined_groups[0]
        # combined_program should be valid QASM
        assert "OPENQASM" in cg["combined_program"]

        info = cg["combine_info"]
        # assigned_ids in combine_info should match
        assert set(info["assigned_ids"]) == {"job-1", "job-2", "job-3"}
        # assigned_group should contain the original job details
        assert len(info["assigned_group"]) == 3
        for job_detail in info["assigned_group"]:
            assert job_detail["job_id"] in {"job-1", "job-2", "job-3"}
            assert job_detail["qubit_mapping"] is not None
        assert info["combined_qubits_list"] == [2, 1, 3]
        # n_total_qubits = max physical qubit index + 1, so at least 6
        assert info["n_total_qubits"] >= 6

    def test_end_to_end_assign_and_combine_2groups(self):
        combiner = OptimalCircuitCombiner()
        topology = make_grid_topology(3,3)
        jobs = [
            {"job_id": "job-1", "program": SIMPLE_2Q_QASM},
            {"job_id": "job-2", "program": SIMPLE_1Q_QASM},
            {"job_id": "job-3", "program": SIMPLE_3Q_QASM},
            {"job_id": "job-4", "program": UNASSIGNABLE_3Q_QASM},  # Unassignable job
            {"job_id": "job-5", "program": SIMPLE_2Q_QASM},
            {"job_id": "job-6", "program": SIMPLE_3Q_QASM},
        ]

        assigned_ids, assigned_groups = combiner.assign_circuits(jobs, topology)
        combined_groups = combiner.combine_circuits_for_groups(assigned_groups)

        # Both jobs should be assigned
        assert set(assigned_ids) == {"job-1", "job-2", "job-3", "job-5", "job-6"}
        # Jobs should be split into 2 groups due to topology constraints
        assert len(assigned_groups) == 2
        assert len(combined_groups) == 2

        def validate(cg, expected_job_ids, expected_qubits_list):
            assert "OPENQASM" in cg["combined_program"]
            info = cg["combine_info"]
            assert set(info["assigned_ids"]) == expected_job_ids
            assert len(info["assigned_group"]) == len(expected_job_ids)
            for job_detail in info["assigned_group"]:
                assert job_detail["job_id"] in expected_job_ids
                assert job_detail["qubit_mapping"] is not None
            assert info["combined_qubits_list"] == expected_qubits_list
            # n_total_qubits = max physical qubit index + 1, so at least sum of qubits in group
            assert info["n_total_qubits"] >= sum(expected_qubits_list)

        validate(combined_groups[0], {"job-1", "job-2", "job-3", "job-5"}, [2, 1, 3, 2])
        validate(combined_groups[1], {"job-6"}, [3])
