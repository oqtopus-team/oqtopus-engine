"""Shared behavioral contract tests reused by every assignment strategy test module.

Concrete test classes mix this in and implement ``build_strategy()`` to return an
instance configured so the shared scenarios below reliably succeed.
"""

import pytest

from oqtopus_engine_combiner.assignment.base import AssignmentStrategy
from oqtopus_engine_combiner.mp_auto import (  # type: ignore[import-untyped]
    JobWithCircuitGraph,
    OptimalCircuitCombiner,
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


class AssignmentStrategyContractTests:
    """Behavioral contract shared by every ``AssignmentStrategy`` implementation."""

    def build_strategy(self) -> AssignmentStrategy:
        """Return a strategy instance under test.

        Raises:
            NotImplementedError: Always, unless overridden by a subclass.

        """
        raise NotImplementedError

    def test_single_match(self):
        topology = OptimalCircuitCombiner.create_topology_graph(make_linear_topology(5))
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        results = self.build_strategy().assign(topology, [job])

        assert len(results) == 1
        assert results[0].job_id == "job-1"
        assert len(results[0].mapping) == job.circuit_graph.number_of_nodes()
        for node in results[0].T_nodes:
            assert node in topology.nodes()

    def test_multiple_matches_non_overlapping(self):
        topology = OptimalCircuitCombiner.create_topology_graph(
            make_grid_topology_with_defects()
        )
        jobs = [
            JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM),
            JobWithCircuitGraph(job_id="job-2", program=SIMPLE_1Q_QASM),
            JobWithCircuitGraph(job_id="job-3", program=SIMPLE_3Q_QASM),
        ]

        results = self.build_strategy().assign(topology, jobs)

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

    def test_partial_match_on_small_topology(self):
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

        results = self.build_strategy().assign(topology, jobs)

        matched_ids = {r.job_id for r in results}
        assert len(results) == 2
        assert "job-1" in matched_ids
        assert "job-2" not in matched_ids
        assert "job-3" in matched_ids

        t_edges = set(topology.edges())
        for r in results:
            job = next(j for j in jobs if j.job_id == r.job_id)
            assert len(r.T_nodes) == job.circuit_graph.number_of_nodes()
            for u, v in job.circuit_graph.edges():
                assert (r.mapping[int(u)], r.mapping[int(v)]) in t_edges

        all_t_nodes = [set(r.T_nodes) for r in results]
        for i in range(len(all_t_nodes)):
            for j in range(i + 1, len(all_t_nodes)):
                assert all_t_nodes[i].isdisjoint(all_t_nodes[j])

    def test_unassignable_job_is_skipped(self):
        """A directed chain of two same-facing edges can never embed on the grid."""
        topology = OptimalCircuitCombiner.create_topology_graph(make_grid_topology(3, 3))
        jobs = [
            JobWithCircuitGraph(job_id="job-1", program=UNASSIGNABLE_3Q_QASM),
            JobWithCircuitGraph(job_id="job-2", program=SIMPLE_1Q_QASM),
        ]

        results = self.build_strategy().assign(topology, jobs)

        matched_ids = {r.job_id for r in results}
        assert "job-1" not in matched_ids
        assert "job-2" in matched_ids

    def test_no_match_when_topology_too_small(self):
        topology = OptimalCircuitCombiner.create_topology_graph(
            {"qubits": [{"id": 0, "position": {"x": 0, "y": 0}}], "couplings": []}
        )
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        results = self.build_strategy().assign(topology, [job])

        assert len(results) == 0

    def test_idle_qubits_insertion_avoids_neighboring_nodes(self):
        topology_json = make_linear_topology(7)
        topology = OptimalCircuitCombiner.create_topology_graph(topology_json)
        inferred_topology = OptimalCircuitCombiner.create_device_grid_graph(
            topology_json
        )
        jobs = [
            JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM),
            JobWithCircuitGraph(job_id="job-2", program=SIMPLE_2Q_QASM),
        ]

        results = self.build_strategy().assign(
            topology,
            jobs,
            inferred_topology,
            idle_qubits_insertion_enabled=True,
        )

        assert len(results) == 2
        job1_nodes, job2_nodes = (set(r.T_nodes) for r in results)
        assert job1_nodes.isdisjoint(job2_nodes)
        # Idle qubit insertion keeps a buffer node between assigned groups, so no
        # node of one job should be directly adjacent to a node of the other.
        for u, v in inferred_topology.edges():
            assert not (u in job1_nodes and v in job2_nodes)
            assert not (u in job2_nodes and v in job1_nodes)


class IdleQubitsUnsupportedContractTests:
    """Shared contract for strategies that reject idle qubit insertion."""

    def build_strategy(self) -> AssignmentStrategy:
        """Return a strategy instance under test.

        Raises:
            NotImplementedError: Always, unless overridden by a subclass.

        """
        raise NotImplementedError

    def test_idle_qubits_insertion_not_supported(self):
        topology = OptimalCircuitCombiner.create_topology_graph(make_linear_topology(5))
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        with pytest.raises(NotImplementedError):
            self.build_strategy().assign(
                topology, [job], idle_qubits_insertion_enabled=True
            )
