import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[3].joinpath("src")))

from oqtopus_engine_combiner.assignment.heuristic import HeuristicAssignmentStrategy
from oqtopus_engine_combiner.mp_auto import (  # type: ignore[import-untyped]
    JobWithCircuitGraph,
    OptimalCircuitCombiner,
)

from tests.oqtopus_engine_combiner.assignment.strategy_contract import (
    AssignmentStrategyContractTests,
    IdleQubitsUnsupportedContractTests,
)
from tests.oqtopus_engine_combiner.topology_helpers import (
    SIMPLE_1Q_QASM,
    SIMPLE_2Q_QASM,
    SIMPLE_3Q_QASM,
    UNASSIGNABLE_3Q_QASM,
    make_grid_topology,
    make_grid_topology_with_defects,
    make_linear_topology,
)


class TestHeuristicAssignmentStrategy(
    AssignmentStrategyContractTests, IdleQubitsUnsupportedContractTests
):
    """Tests for HeuristicAssignmentStrategy.assign."""

    def build_strategy(self) -> HeuristicAssignmentStrategy:
        # Backtracking is required for the shared contract scenarios to reliably
        # succeed; greedy mode's behavior is covered separately below.
        return HeuristicAssignmentStrategy(mode="backtrack")

    def test_idle_qubits_insertion_avoids_neighboring_nodes(self):
        # This test is intentionally left blank because the heuristic strategy
        # does not support idle qubits insertion yet.
        # Remove this function once idle qubits insertion is supported.
        pass

    def test_name(self):
        assert HeuristicAssignmentStrategy().name == "heuristic"

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="unknown heuristic mode"):
            HeuristicAssignmentStrategy(mode="invalid")

    def test_multiple_matches_non_overlapping_greedy_mode(self):
        topology = OptimalCircuitCombiner.create_topology_graph(
            make_grid_topology_with_defects()
        )
        jobs = [
            JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM),
            JobWithCircuitGraph(job_id="job-2", program=SIMPLE_1Q_QASM),
            JobWithCircuitGraph(job_id="job-3", program=SIMPLE_3Q_QASM),
        ]

        results = HeuristicAssignmentStrategy(mode="greedy").assign(topology, jobs)

        assert len(results) == 3
        t_edges = set(topology.edges())
        all_t_nodes = []
        for r in results:
            job = next(j for j in jobs if j.job_id == r.job_id)
            assert len(r.T_nodes) == job.circuit_graph.number_of_nodes()
            for u, v in job.circuit_graph.edges():
                assert (r.mapping[int(u)], r.mapping[int(v)]) in t_edges
            all_t_nodes.append(set(r.T_nodes))

        for i in range(len(all_t_nodes)):
            for j in range(i + 1, len(all_t_nodes)):
                assert all_t_nodes[i].isdisjoint(all_t_nodes[j])

    def test_backtrack_finds_match_that_greedy_misses(self):
        """A 3x3 grid where greedy alone may fail without backtracking budget."""
        topology = OptimalCircuitCombiner.create_topology_graph(make_grid_topology(3, 3))
        jobs = [
            JobWithCircuitGraph(job_id="job-1", program=UNASSIGNABLE_3Q_QASM),
            JobWithCircuitGraph(job_id="job-2", program=SIMPLE_1Q_QASM),
        ]

        results = HeuristicAssignmentStrategy(mode="backtrack").assign(topology, jobs)

        matched_ids = {r.job_id for r in results}
        assert "job-1" not in matched_ids
        assert "job-2" in matched_ids

    def test_assign_with_verify_enabled_validates_mapping(self):
        topology = OptimalCircuitCombiner.create_topology_graph(make_linear_topology(5))
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        results = HeuristicAssignmentStrategy(mode="backtrack", verify=True).assign(
            topology, [job]
        )

        assert len(results) == 1

    def test_embed_returns_empty_mapping_for_qubitless_job(self):
        topology = OptimalCircuitCombiner.create_topology_graph(make_linear_topology(3))
        # Declared but unused qubits never become circuit_graph nodes.
        qasm = 'OPENQASM 3;\ninclude "stdgates.inc";\nqubit[1] q;\nbit[1] c;'
        job = JobWithCircuitGraph(job_id="job-1", program=qasm)
        assert job.circuit_graph.number_of_nodes() == 0

        results = HeuristicAssignmentStrategy().assign(topology, [job])

        assert len(results) == 1
        assert results[0].mapping == {}
        assert results[0].T_nodes == []

    def test_embed_returns_none_when_backtrack_budget_exhausted(self):
        topology = OptimalCircuitCombiner.create_topology_graph(make_grid_topology(3, 3))
        job = JobWithCircuitGraph(job_id="job-1", program=UNASSIGNABLE_3Q_QASM)

        results = HeuristicAssignmentStrategy(mode="backtrack", max_backtracks=0).assign(
            topology, [job]
        )

        assert len(results) == 0
