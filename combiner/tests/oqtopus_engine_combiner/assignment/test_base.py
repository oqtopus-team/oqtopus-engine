import sys
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[3].joinpath("src")))

import networkx as nx  # type: ignore[import-untyped]
import pytest

from oqtopus_engine_combiner.assignment.base import (
    AssignmentMatch,
    AssignmentStrategyBase,
    validate_assignment,
)
from oqtopus_engine_combiner.mp_auto import (  # type: ignore[import-untyped]
    JobWithCircuitGraph,
    OptimalCircuitCombiner,
)

from tests.oqtopus_engine_combiner.topology_helpers import (
    SIMPLE_1Q_QASM,
    SIMPLE_2Q_QASM,
    make_grid_topology,
    make_grid_topology_with_defects,
    make_linear_topology,
)


# ===================================================================
# Tests for AssignmentMatch
# ===================================================================


class TestAssignmentMatch:
    """Tests for the AssignmentMatch dataclass."""

    def test_getitem_returns_attributes(self):
        match = AssignmentMatch(
            G_index=0, job_id="job-1", mapping={0: 1}, T_nodes=[1]
        )

        assert match["G_index"] == 0
        assert match["job_id"] == "job-1"
        assert match["mapping"] == {0: 1}
        assert match["T_nodes"] == [1]


# ===================================================================
# Tests for AssignmentStrategyBase._calculate_idle_nodes_before_mapping
# ===================================================================


class TestCalculateIdleNodesBeforeMapping:
    """Tests for AssignmentStrategyBase._calculate_idle_nodes_before_mapping."""

    def test_with_non_edge_of_g(self):
        topology_json = make_linear_topology(5)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_1Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_before_mapping(
            set(), set(), topology, job.circuit_graph
        )

        assert idle_nodes == set()

    def test_with_empty_exist_idle_nodes_and_used_nodes(self):
        topology_json = make_linear_topology(5)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_before_mapping(
            set(), set(), topology, job.circuit_graph
        )

        assert idle_nodes == set()

    def test_with_exist_idle_nodes_but_no_used_nodes(self):
        topology_json = make_linear_topology(5)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        with patch("oqtopus_engine_combiner.assignment.base.logger.info") as mock_info:
            idle_nodes = strategy._calculate_idle_nodes_before_mapping(
                set(), {0, 1}, topology, job.circuit_graph
            )

        assert idle_nodes == set()
        mock_info.assert_called_once_with(
            "Exist idle nodes but no used nodes.",
            extra={
                "exist_idle_nodes": {0, 1},
                "used_nodes": set(),
            },
        )

    def test_with_none_inferred_topology(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        with patch("oqtopus_engine_combiner.assignment.base.logger.info") as mock_info:
            idle_nodes = strategy._calculate_idle_nodes_before_mapping(
                {0}, set(), None, job.circuit_graph
            )

        assert idle_nodes == set()
        mock_info.assert_called_once_with(
            "Inferred topology is None, cannot calculate idle nodes before mapping"
        )

    def test_with_only_used_nodes(self):
        topology_json = make_grid_topology(3, 4)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_before_mapping(
            {5, 6}, set(), topology, job.circuit_graph
        )

        assert idle_nodes == {1, 2, 4, 7, 9, 10}

    def test_with_used_nodes_and_exist_idle_nodes(self):
        topology_json = make_grid_topology(3, 4)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_before_mapping(
            {5, 6}, {1, 2, 4, 7, 9, 10}, topology, job.circuit_graph
        )

        assert idle_nodes == set()

    def test_with_only_used_nodes_and_defects_topology(self):
        topology_json = make_grid_topology_with_defects()
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)

        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_before_mapping(
            {19, 36}, set(), topology, job.circuit_graph
        )

        assert idle_nodes == {11, 18, 20, 27, 28, 35, 37, 44}

    def test_with_used_nodes_and_exist_idle_nodes_and_defects_topology(self):
        topology_json = make_grid_topology_with_defects()
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_before_mapping(
            {19, 35, 36}, {27, 28, 34, 37, 43, 44}, topology, job.circuit_graph
        )

        assert idle_nodes == {11, 18, 20}


# ===================================================================
# Tests for AssignmentStrategyBase._calculate_idle_nodes_after_mapping
# ===================================================================


class TestCalculateIdleNodesAfterMapping:
    """Tests for AssignmentStrategyBase._calculate_idle_nodes_after_mapping."""

    def test_with_non_edge_of_g(self):
        topology_json = make_linear_topology(5)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_1Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_after_mapping(
            set(), set(), topology, job.circuit_graph, {1: 1}
        )

        assert idle_nodes == set()

    def test_edge_endpoint_not_in_result_mapping(self):
        topology_json = make_linear_topology(5)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        with patch("oqtopus_engine_combiner.assignment.base.logger.info") as mock_info:
            idle_nodes = strategy._calculate_idle_nodes_after_mapping(
                set(), set(), topology, job.circuit_graph, {0: 0, 100: 1}
            )

        assert idle_nodes == set()
        mock_info.assert_called_once_with(
            "Edge endpoint not in result mapping, this should not happen",
            extra={
                "edge_node": 1,
                "result_mapping": {0: 0, 100: 1},
            },
        )

    def test_with_none_inferred_topology(self):
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        with patch("oqtopus_engine_combiner.assignment.base.logger.info") as mock_info:
            idle_nodes = strategy._calculate_idle_nodes_after_mapping(
                set(), set(), None, job.circuit_graph, {0: 0, 1: 1}
            )

        assert idle_nodes == set()
        mock_info.assert_called_once_with(
            "Inferred topology is None, cannot calculate idle nodes after mapping"
        )

    def test_with_non_used_nodes_and_exist_idle_nodes(self):
        topology_json = make_grid_topology(3, 4)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_after_mapping(
            set(), set(), topology, job.circuit_graph, {0: 0, 1: 1}
        )

        assert idle_nodes == {2, 4, 5}

    def test_with_only_used_nodes(self):
        topology_json = make_grid_topology(3, 4)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_after_mapping(
            {6}, set(), topology, job.circuit_graph, {0: 0, 1: 1}
        )

        assert idle_nodes == {2, 4, 5}

    def test_with_used_nodes_and_exist_idle_nodes(self):
        topology_json = make_grid_topology(3, 4)
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_after_mapping(
            {8, 9}, {4, 5, 10}, topology, job.circuit_graph, {0: 0, 1: 1}
        )

        assert idle_nodes == {2}

    def test_with_only_used_nodes_on_defects_topology(self):
        topology_json = make_grid_topology_with_defects()
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_after_mapping(
            {17}, set(), topology, job.circuit_graph, {0: 18, 1: 19}
        )

        assert idle_nodes == {10, 11, 20, 26, 27}

    def test_with_used_nodes_and_exist_idle_nodes_on_defects_topology(self):
        topology_json = make_grid_topology_with_defects()
        topology = OptimalCircuitCombiner.create_device_grid_graph(topology_json)
        job = JobWithCircuitGraph(job_id="job-1", program=SIMPLE_2Q_QASM)

        strategy = AssignmentStrategyBase()
        idle_nodes = strategy._calculate_idle_nodes_after_mapping(
            {21, 22}, {13, 14, 20, 23, 29, 30}, topology, job.circuit_graph, {0: 18, 1: 19}
        )

        assert idle_nodes == {10, 11, 17, 26, 27}


# ===================================================================
# Tests for validate_assignment
# ===================================================================


class TestValidateAssignment:
    """Tests for the validate_assignment helper."""

    def test_valid_mapping_does_not_raise(self):
        t = nx.DiGraph([(0, 1), (1, 2)])
        g = nx.DiGraph([(0, 1)])
        mapping = {0: 0, 1: 1}

        validate_assignment(t, g, mapping, used_before=set())

    def test_mapping_missing_node_raises(self):
        t = nx.DiGraph([(0, 1)])
        g = nx.DiGraph([(0, 1)])
        mapping = {0: 0}

        with pytest.raises(AssertionError, match="does not cover the circuit graph"):
            validate_assignment(t, g, mapping, used_before=set())

    def test_mapping_duplicate_physical_node_raises(self):
        t = nx.DiGraph([(0, 1)])
        g = nx.DiGraph()
        g.add_nodes_from([0, 1])
        mapping = {0: 0, 1: 0}

        with pytest.raises(AssertionError, match="more than once"):
            validate_assignment(t, g, mapping, used_before=set())

    def test_mapping_with_nonexistent_topology_edge_raises(self):
        t = nx.DiGraph([(0, 1)])
        g = nx.DiGraph([(0, 1)])
        mapping = {0: 1, 1: 0}

        with pytest.raises(AssertionError, match="non-existent topology edge"):
            validate_assignment(t, g, mapping, used_before=set())

    def test_mapping_overlapping_used_nodes_raises(self):
        t = nx.DiGraph([(0, 1)])
        g = nx.DiGraph([(0, 1)])
        mapping = {0: 0, 1: 1}

        with pytest.raises(AssertionError, match="overlaps previously used"):
            validate_assignment(t, g, mapping, used_before={1})
