import logging
import operator
from typing import Any

import matplotlib.pyplot as plt
import networkx as nx  # type: ignore[import-untyped]
import qiskit.qasm3  # type: ignore[import-untyped]
from ortools.sat.python import cp_model
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister

logger = logging.getLogger(__name__)

TARGET_SIZE = 30
DEBUG_DRAW_GRAPH = False


class JobWithCircuitGraph:
    """Job class containing circuit graph and qubit mappings.

    Attributes:
        job_id: Job identifier (str).
        program: Quantum circuit in QASM format (str).
        circuit_graph: NetworkX graph representing the circuit.
        original_compress_mapping: Mapping of original qubits vs compressed one (dict).
        compress_matching_mapping: Mapping of compressed vs assigned qubits (dict).

    """

    def __init__(self,
                 job_id: str,
                 program: str,
        ) -> None:
        self.job_id = job_id
        self.program = program
        self.qiskit_circuit = qiskit.qasm3.loads(program)

        # Qubit mappings are complicated. There are three types of mappings:
        # 1. original_to_graph_mapping:
        #                A qubit index map from input circuit to graph nodes
        # 2. graph_matching_mapping:
        #                A qubit index map from graph nodes to physical qubits
        # 3. original_matching_mapping:
        #                A qubit index map from input circuit to physical qubits
        # We store the first two mappings, and compute the third one when needed.
        # It is because the first two mappings are needed during the combining process,
        # and the third one can be derived from the first two.
        # The mapping returned to client is the third one.

        # key: qubit index in input circuit
        # value: qubit index in circuitgraph nodes
        self.original_to_graph_mapping: dict[int, int] | None = None
        # key: qubit index in circuit graph nodes
        # value: matched physical qubit index in topology graph
        self.graph_matching_mapping: dict[int, int] | None = None
        # key: qubit index in input circuit
        # value: assigned physical qubit index
        self.original_matching_mapping: dict[int, int] | None = None

        self.circuit_graph, self.original_to_graph_mapping = \
                                self._quantum_circuit_to_networkx(self.qiskit_circuit)

    def update_original_matching_mapping(self) -> None:
        """Compute original-matching mapping from compress and matching mappings."""
        if self.graph_matching_mapping is not None \
           and self.original_to_graph_mapping is not None:
            self.original_matching_mapping = \
                                    {k: self.graph_matching_mapping[v]
                                     for k, v in self.original_to_graph_mapping.items()
                                    }

    @staticmethod
    def _quantum_circuit_to_networkx(circuit: QuantumCircuit) -> nx.MultiDiGraph:
        """Convert QuantumCircuit to networkx graph.

        This function converts a Qiskit QuantumCircuit into a networkx MultiDiGraph.
        Unused qubits are ignored in the graph representation.

        Args:
            circuit: QuantumCircuit object.

        Returns:
            A networkx MultiDiGraph representing the circuit.

        """
        g = nx.MultiDiGraph()
        qubit_to_node_mapping = {}
        node_index = 0
        for instruction in circuit.data:
            qubit_indices = [circuit.find_bit(qubit).index
                             for qubit in instruction.qubits
                             ]
            # add nodes for qubits if not already added
            for qubit_index in qubit_indices:
                if qubit_index not in qubit_to_node_mapping:
                    g.add_node(node_index)
                    qubit_to_node_mapping[qubit_index] = node_index
                    node_index += 1
            # add edges for multi-qubit gates
            if instruction.operation.name == "cx":
                control_index = qubit_to_node_mapping[circuit.
                                                      find_bit(instruction.qubits[0]).index]
                target_index = qubit_to_node_mapping[circuit.
                                                     find_bit(instruction.qubits[1]).index]
                g.add_edge(control_index, target_index)

        return g, qubit_to_node_mapping

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dict for serialization when returning results to client.

        Returns:
            A dictionary containing job_id, program, and total_qubits_mapping.

        """
        return {
            "job_id": self.job_id,
            "program": self.program,
            "qubit_mapping": self.original_matching_mapping,
        }


class OptimalCircuitCombiner:
    """Combines quantum circuits optimally based on device topology.

    This class assigns qubits to quantum circuits based on the device topology
    and combines them into larger circuits to maximize resource utilization.

    """

    @staticmethod
    def create_topology_graph(topology_json: dict[str, Any]) -> nx.Graph:
        """Create a networkx graph from the device topology JSON.

        Args:
            topology_json: Device topology in JSON format.

        Returns:
            A networkx graph representing the device topology.

        """
        qubits = topology_json["qubits"]
        couplings = topology_json["couplings"]

        g = nx.MultiDiGraph()
        for qubit in qubits:
            g.add_node(qubit["id"])
        for coupling in couplings:
            g.add_edge(coupling["control"], coupling["target"])

        return g

    def combine_circuits_for_groups(self,
                                    assigned_groups: list
                                    ) -> list[dict[str, Any]]:
        """Combine circuits for each assigned group.

        Args:
            assigned_groups: List of groups of jobs to be combined.

        Returns:
            List of combined groups with combined program and combine info.
            Combine info includes assigned job IDs, assigned group details,
            a list of the number of qubits used in each original circuit,
            and total number of qubits.

        """
        combined_groups = []
        for group in assigned_groups:
            # combine circuits into one circuit
            combined_qasm, combined_qubits_list, n_total_qubits = \
                                                self.combine_circuits(group)

            combine_info = {
                "assigned_ids": [job.job_id for job in group],
                "assigned_group": [job.to_dict() for job in group],
                "combined_qubits_list": combined_qubits_list,
                "n_total_qubits": n_total_qubits,
            }

            combined_groups.append({
                "combined_program": combined_qasm,
                "combine_info": combine_info,
            })
        return combined_groups

    def combine_circuits(self, grouped_jobs: list) -> tuple[str, list[int], int]:
        """Combine multiple quantum circuits into a single circuit.

        Args:
            grouped_jobs: List of JobWithCircuitGraph instances to be combined.

        Returns:
            A tuple containing:
            - The combined quantum circuit in QASM format (str).
            - A list of integers representing the number of qubits
               used in each original circuit.
            - The total number of qubits in the combined circuit (int).

        """
        # get the max qubit index of all circuits
        max_qubit_index = max(
            max(job.graph_matching_mapping.values()) for job in grouped_jobs
        )
        # create a new quantum circuit with the max qubit index
        n_classical_bits = sum(job.qiskit_circuit.num_clbits
                                for job in grouped_jobs
                              )
        qr = QuantumRegister(max_qubit_index + 1, name="q")
        cr = ClassicalRegister(n_classical_bits, name="c")
        qc = QuantumCircuit(qr, cr)

        # list to store measurement operations for each circuit
        measure_ops_list = []
        # list to store the number of qubits used in each circuit
        combined_qubits_list = []

        # copy each circuit into the new circuit with remapped qubits
        for job in grouped_jobs:
            circuit = qiskit.qasm3.loads(job.program)
            qc, measure_ops = self._copy_gates_with_mapping(
                source_circuit=circuit,
                target_circuit=qc,
                qubit_mapping=job.original_matching_mapping,
            )

            # record the number of classical bits used in this circuit.
            # the number of classical bits is assumed to be equal to
            # the length of measured bitstrings and is neccessary for dividing.
            # the number of qubits is not always equal to that of classical bits,
            # for example, in the case of circuits with ancilla qubits added by
            # transpiler.
            combined_qubits_list.append(job.qiskit_circuit.num_clbits)
            # sort measure_ops by classical bit index and add to measure_ops_list
            measure_ops_list.append(sorted(measure_ops, key=operator.itemgetter(2)))

        # add measurement operations at the end remapping classical bits
        final_measure_ops = []
        clidx = 0
        for measure_ops in measure_ops_list:
            # map classical bits to new indices
            clidx_map = {}
            for measure_instr, q_regs, idx in measure_ops:
                if idx not in clidx_map:
                    # assign new classical bit index if not mapped yet
                    clidx_map[idx] = clidx
                    clidx += 1
                # append measurement operation with remapped classical bit index
                final_measure_ops.append((measure_instr, q_regs, [clidx_map[idx]]))

        for measure_instr, qargs, idx in final_measure_ops:
            qc.append(measure_instr, qargs, idx)

        return qiskit.qasm3.dumps(qc), combined_qubits_list, qc.num_qubits

    @staticmethod
    def _copy_gates_with_mapping(source_circuit: QuantumCircuit,
                                 target_circuit: QuantumCircuit,
                                 qubit_mapping: dict[int, int]
                                 ) -> tuple[QuantumCircuit,
                                            list[tuple[Any, list[Any], list[Any]]]
                                            ]:
        """Copy gates from source circuit to target circuit with qubit mapping.

        This function copies gates from the source quantum circuit
        to the target quantum circuit according to the provided qubit mapping.
        Measurement operations are collected separately to be added later.

        Args:
            source_circuit: Source QuantumCircuit to copy from.
            target_circuit: Target QuantumCircuit to copy to.
            qubit_mapping: Mapping from source qubit indices to target qubit indices.

        Returns:
            A tuple containing:
            - The target QuantumCircuit with copied gates.
            - A list of measurement operations to be added later.

        """
        qc = target_circuit.copy()
        measure_ops = []
        # copy gates to the new circuit with remapped qubits
        for instr, qargs, cargs in source_circuit.data:
            q_regs = []
            for q in qargs:
                idx = source_circuit.find_bit(q).index
                target_idx = qubit_mapping[idx]
                q_regs.append(qc.qregs[0][target_idx])

            if instr.name == "measure":
                measure_ops.append((instr, q_regs,
                                    source_circuit.find_bit(cargs[0]).index)
                                    )
            else:
                qc.append(instr, q_regs, [])
        return qc, measure_ops

    def assign_circuits(self,
                        jobs: list[dict[str, str]],
                        device_info: dict[str, Any]
                        ) -> tuple[list[str], list[list[JobWithCircuitGraph]]]:
        """Assign qubits to each circuit based on the device topology.

        Args:
            jobs: List of jobs, each containing 'job_id' and 'program' in QASM format.
            device_info: Device topology information in JSON format.

        Returns:
            A tuple containing:
            - A list of assigned job IDs (list of str).
            - A list of groups of jobs that have been assigned qubits
              (list of list of JobWithCircuitGraph).
              Each group can be combined into a single circuit.

        """
        # convert QPU topology to networkx graph
        topology = self.create_topology_graph(topology_json=device_info)

        # assigned job_id list
        assigned_ids = set()
        # assigned job list
        assigned_groups = []

        # unassigned job list
        unassigned_jobs = [JobWithCircuitGraph(job_id=job["job_id"],
                                               program=job["program"]
                                               )
                           for job in jobs
                           ]

        previous_jobs_num = len(unassigned_jobs)
        while unassigned_jobs:
            # extract up to TARGET_SIZE jobs
            # TODO: better to determine the size dynamically # noqa: FIX002,TD003,TD002
            current_batch = unassigned_jobs[:TARGET_SIZE]
            logger.info(
                "searching circuit",
                extra={
                    "job_id_from": current_batch[0].job_id,
                    "job_id_to": current_batch[-1].job_id,
                },
            )

            matches = self._find_nonoverlapping_subgraphs_with_t_nodes(topology,
                                                                 current_batch
                                                                 )
            assigned_group = []
            for match in matches:
                idx = match["G_index"]
                job = current_batch[idx]
                job.graph_matching_mapping = match["mapping"]
                job.update_original_matching_mapping()
                assigned_group.append(job)
                assigned_ids.add(job.job_id)

            if assigned_group:
                assigned_groups.append(assigned_group)
                if DEBUG_DRAW_GRAPH:
                    self._draw_graph(topology, device_info, matches,
                                     f"device_topology_with_assigned_nodes_{len(assigned_groups)}.png"
                                     )

            # exclude assigned jobs from unassigned_jobs
            unassigned_jobs = [circuit
                               for circuit in unassigned_jobs
                               if circuit.job_id not in assigned_ids
                               ]

            # break if no more assignments can be made
            if previous_jobs_num == len(unassigned_jobs):
                logger.info("no more assignments can be made")
                break
            previous_jobs_num = len(unassigned_jobs)

        return list(assigned_ids), assigned_groups

    @staticmethod
    def _draw_graph(g: nx.MultiDiGraph,
                    topology_json: dict,
                    matches: list[dict[str, Any]],
                    filename: str
                    ) -> None:
        """Draw the topology graph with assigned nodes highlighted for debugging."""
        try:
            colors = ["Red", "Green", "Blue", "Purple", "Magenta",
                      "Cyan", "Orange", "Yellow", "Brown", "Pink",
                      "Lime", "Teal", "Lavender", "Olive", "Maroon",
                      "Navy", "Grey", "White", "Aqua", "Coral"
                     ]
            node_colors = ["black"] * g.number_of_nodes()
            pos = {}
            for qubit in topology_json["qubits"]:
                position = qubit["position"]
                pos[qubit["id"]] = (position["x"], position["y"])

            for i, match in enumerate(matches):
                for node in match["T_nodes"]:
                    node_colors[node] = colors[i]

            nx.draw(g, pos=pos, with_labels=True, node_color=node_colors)
            plt.savefig(filename)
            plt.close()
        except Exception:
            logger.exception("failed to draw graph for debugging")

    @staticmethod
    def _find_nonoverlapping_subgraphs_with_t_nodes(t: nx.Graph,
                                                    jobs: list[JobWithCircuitGraph]
                                                    ) -> list[dict[str, Any]]:
        """Find subgraphs in jobs' circuit graphs that can be mapped to T.

        Args:
            t: Target graph T representing the device connectivity.
            jobs: List of job dictionaries, each containing a 'circuit_graph' key
                with the circuit's graph.

        Returns:
            A list of dictionaries for each job that can be mapped to T, containing:
                - "G_index": Index of the job in the input list.
                - "job_id": The job's identifier.
                - "mapping": A dictionary mapping nodes of G to nodes of T.
                - "T_nodes": List of T nodes used in the mapping.

        """
        used_nodes: set[int] = set()
        results = []

        for idx, job in enumerate(jobs):
            g = job.circuit_graph

            model = cp_model.CpModel()
            n_g = g.number_of_nodes()
            n_t = t.number_of_nodes()

            # Variables that assign nodes of T to each node of G
            mapping = [model.NewIntVar(0, n_t - 1, f"map_{i}") for i in range(n_g)]
            model.AddAllDifferent(mapping)

            # Add constraints to prevent used nodes from being assigned
            for m in mapping:
                for used in used_nodes:
                    model.Add(m != used)

            # Get the set of edges in T and create allowed pairs for mapping
            t_edges_set = set(t.edges())
            # if undirected graph (direction of qubit connections does not matter),
            # uncomment the following line to add reverse edges
            allowed_pairs = list(t_edges_set)  # + [(b, a) for (a, b) in t_edges_set]

            # Add constraints to ensure edges in G map to edges in T
            for u, v in g.edges():
                model.AddAllowedAssignments([mapping[int(u)],
                                            mapping[int(v)]],
                                            allowed_pairs
                                            )

            # run solver
            solver = cp_model.CpSolver()
            status = solver.Solve(model)
            logger.debug(
                "running solver",
                extra={
                    "job_id": job.job_id,
                    "status": status,
                }
            )

            if status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                result_mapping = {i: solver.Value(mapping[i]) for i in range(n_g)}
                mapped_t_nodes = list(set(result_mapping.values()))

                results.append({
                    "G_index": idx,
                    "job_id": job.job_id,
                    "mapping": result_mapping,
                    "T_nodes": mapped_t_nodes
                })

                used_nodes.update(mapped_t_nodes)
            else:
                logger.debug(
                    "job does not match",
                    extra={
                        "job_id": job.job_id,
                        "index": idx,
                    }
                )

        return results
