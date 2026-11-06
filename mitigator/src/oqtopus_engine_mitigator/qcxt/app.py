import json
import logging
from itertools import product
from typing import Any

import grpc  # type: ignore[import-untyped]
import numpy as np
from qiskit import QuantumCircuit, qasm3  # type: ignore[import-untyped]
from qiskit.circuit.equivalence_library import (  # type: ignore[import-untyped]
    SessionEquivalenceLibrary,
)
from qiskit.circuit.library import XGate, ZGate  # type: ignore[import-untyped]
from qiskit.transpiler import PassManager  # type: ignore[import-untyped]
from qiskit.transpiler.passes import (  # type: ignore[import-untyped]
    BasisTranslator,
    UnrollCustomDefinitions,
)

from oqtopus_engine_core.interfaces.mitigator_interface.v1 import mitigator_pb2_grpc
from oqtopus_engine_core.interfaces.mitigator_interface.v1.mitigator_pb2 import (  # type: ignore[attr-defined]
    ReqQcxtPostProcessRequest,
    ReqQcxtPostProcessResponse,
    ReqQcxtPreProcessRequest,
    ReqQcxtPreProcessResponse,
)
from oqtopus_engine_mitigator.qcxt.device import DeviceData
from oqtopus_engine_mitigator.qcxt.simulator import Simulator
from oqtopus_engine_mitigator.qcxt.solver import CutSelector, SelectedCut

logger = logging.getLogger(__name__)

_GATE_MAP: dict[str, XGate | ZGate] = {
    "X": XGate(),
    "Z": ZGate(),
}

# CX gate decomposition coefficients and corresponding Pauli operators for gate-cutting.
_CX_DECOMPOSITION: tuple[tuple[float, str, str], ...] = (
    (0.5, "I", "I"),
    (0.5, "Z", "I"),
    (0.5, "I", "X"),
    (-0.5, "Z", "X"),
)

# Each side of a bipartition cannot require more than half of the total fidelity.
_MAX_PARTITION_RATIO = 0.5


def _translate_single_pauli_to_basis(
    pauli: str,
    basis_gates: list[str],
) -> list[Any]:
    """Translate a single-qubit Pauli gate to operations in the target basis.

    Returns:
        A list of operations in the target basis equivalent to the given Pauli gate.

    """
    if pauli == "I":
        return []

    pm = PassManager([
        UnrollCustomDefinitions(SessionEquivalenceLibrary),
        BasisTranslator(SessionEquivalenceLibrary, basis_gates),
    ])

    one_qubit_circuit = QuantumCircuit(1)
    one_qubit_circuit.append(_GATE_MAP[pauli], [0])
    translated = pm.run(one_qubit_circuit)

    return [instruction.operation for instruction in translated.data]


def _build_cx_ordinal_by_instruction_index(qc: QuantumCircuit) -> dict[int, int]:
    """Map instruction indices to 0-based CX ordinals.

    Returns:
        A map from instruction index to its 0-based ordinal among CX gates.

    """
    cx_ordinal_by_instruction_index: dict[int, int] = {}
    cx_ordinal = 0
    for idx, instruction in enumerate(qc.data):
        if instruction.operation.name == "cx":
            cx_ordinal_by_instruction_index[idx] = cx_ordinal
            cx_ordinal += 1
    return cx_ordinal_by_instruction_index


def _resolve_cut_cx_indices(
    cut_pairs: list[SelectedCut],
    cx_ordinal_by_instruction_index: dict[int, int],
) -> list[int]:
    """Resolve selected cut instruction indices to 0-based CX ordinals.

    Returns:
        0-based CX ordinals for each selected cut, in cut_pairs order.

    Raises:
        ValueError: If any selected cut points to a non-CX instruction.

    """
    cut_cx_indices: list[int] = []
    for cut in cut_pairs:
        if cut.instruction_index not in cx_ordinal_by_instruction_index:
            msg = (
                "Selected cut points to a non-CX instruction: "
                f"instruction_index={cut.instruction_index}"
            )
            raise ValueError(msg)
        cut_cx_indices.append(cx_ordinal_by_instruction_index[cut.instruction_index])
    return cut_cx_indices


def _build_coefficient_and_term_map(
    cut_pairs: list[SelectedCut],
    combination: tuple[tuple[float, str, str], ...],
) -> tuple[float, dict[int, tuple[str, str]]]:
    """Build decomposition coefficient and per-cut Pauli replacement map.

    Returns:
        A tuple of the decomposition coefficient and replacement map keyed by
        instruction index.

    """
    coefficient = 1.0
    term_by_index: dict[int, tuple[str, str]] = {}
    for cut, (coeff, ctrl_op, tgt_op) in zip(cut_pairs, combination, strict=True):
        coefficient *= coeff
        term_by_index[cut.instruction_index] = (ctrl_op, tgt_op)
    return coefficient, term_by_index


def _build_decomposed_circuit(
    qc: QuantumCircuit,
    cut_index_set: set[int],
    term_by_index: dict[int, tuple[str, str]],
    translated_pauli_ops: dict[str, list[Any]],
) -> QuantumCircuit:
    """Build a circuit where selected CX gates are replaced by Pauli terms.

    Returns:
        A decomposed circuit preserving the original register structure.

    """
    result = qc.copy_empty_like()
    decomposed_qc = QuantumCircuit(qc.num_qubits, qc.num_clbits)

    for idx, instruction in enumerate(qc.data):
        qubit_indices = [qc.find_bit(q).index for q in instruction.qubits]
        if idx in cut_index_set:
            ctrl_op, tgt_op = term_by_index[idx]
            for op in translated_pauli_ops[ctrl_op]:
                decomposed_qc.append(op, [qubit_indices[0]])
            for op in translated_pauli_ops[tgt_op]:
                decomposed_qc.append(op, [qubit_indices[1]])
        else:
            clbit_indices = [qc.find_bit(c).index for c in instruction.clbits]
            decomposed_qc.append(instruction.operation, qubit_indices, clbit_indices)

    result.compose(decomposed_qc, inplace=True)
    return result


def _cut(
    qc: QuantumCircuit,
    cut_pairs: list[SelectedCut],
    basis_gates: list[str],
) -> tuple[list[QuantumCircuit], list[float], list[int]]:
    """Generate sub-circuits and coefficients based on the selected cuts.

    Args:
        qc: The original QuantumCircuit.
        cut_pairs: List of SelectedCut objects representing the cuts.
        basis_gates: List of basis gates for the circuit.

    Returns:
        A tuple containing a list of sub-circuits, corresponding coefficients,
        and CX ordinals (0-based) for each selected cut in cut_pairs order.

    """
    cut_index_set = {cut.instruction_index for cut in cut_pairs}
    cx_ordinal_by_instruction_index = _build_cx_ordinal_by_instruction_index(qc)
    cut_cx_indices = _resolve_cut_cx_indices(
        cut_pairs,
        cx_ordinal_by_instruction_index,
    )

    sub_circuits: list[QuantumCircuit] = []
    coefficients: list[float] = []

    translated_pauli_ops = {
        "I": [],
        "X": _translate_single_pauli_to_basis("X", basis_gates),
        "Z": _translate_single_pauli_to_basis("Z", basis_gates),
    }

    # Generate sub-circuits for all combinations of cut decompositions
    for combination in product(_CX_DECOMPOSITION, repeat=len(cut_pairs)):
        coefficient, term_by_index = _build_coefficient_and_term_map(
            cut_pairs,
            combination,
        )
        result = _build_decomposed_circuit(
            qc,
            cut_index_set,
            term_by_index,
            translated_pauli_ops,
        )

        sub_circuits.append(result)
        coefficients.append(coefficient)

    return sub_circuits, coefficients, cut_cx_indices


# response
class Qcxt(mitigator_pb2_grpc.QcxtService):
    """Qcxt service implementation for gRPC."""

    def __init__(
        self,
        *,
        max_cuts: int,
        fidelity_threshold: float,
        min_partition_ratio: float,
    ) -> None:
        if max_cuts < 0:
            logger.warning("max_cuts=%d is negative; using 0 (no cuts).", max_cuts)
            max_cuts = 0
        if not 0.0 <= fidelity_threshold <= 1.0:
            logger.warning(
                "fidelity_threshold=%s is outside [0, 1].", fidelity_threshold
            )
        if not 0.0 <= min_partition_ratio <= _MAX_PARTITION_RATIO:
            logger.warning(
                "min_partition_ratio=%s is outside [0, 0.5]; the MIP is "
                "infeasible and falls back to the lowest-fidelity cuts.",
                min_partition_ratio,
            )
        self.max_cuts = max_cuts
        self.fidelity_threshold = fidelity_threshold
        self.min_partition_ratio = min_partition_ratio

    def ReqQcxtPreProcess(  # type: ignore[override]  # ruff: ignore[invalid-function-name]
        self,
        request: ReqQcxtPreProcessRequest,
        context: grpc.ServicerContext,  # ruff: ignore[unused-method-argument]
    ) -> ReqQcxtPreProcessResponse:
        """Handle gRPC request for preprocessing qcxt (gate-cutting) job.

        This method returns the transpiled subprograms, coefficients
        and virtual couplings for estimation job.

        Args:
            request: The gRPC request containing the program,
            device_info and shots for qcxt job.
            context: The gRPC context for the request.

        Returns:
            The gRPC response containing the transpiled subprograms,
            coefficients and virtual couplings for estimation job.

        """
        logger.info("start qcxt preprocess")
        program = request.program
        shots = request.shots
        basis_gates = request.basis_gates
        logger.debug(
            "program:%s, shots:%s, basis_gates:%s",
            request.program,
            request.shots,
            request.basis_gates,
        )
        try:
            device_data = DeviceData.from_dict(json.loads(request.device_info))
            sub_programs, coefficients, selected_cuts = self._preprocess(
                program, device_data, shots, basis_gates
            )
            logger.debug(
                "sub_programs:%s, coefficients:%s, selected_cuts:%s",
                sub_programs,
                coefficients,
                selected_cuts,
            )
            return ReqQcxtPreProcessResponse(
                sub_programs=sub_programs,
                coefficients=coefficients,
                virtual_couplings=[json.dumps(cut) for cut in selected_cuts],
            )
        except Exception:
            logger.exception("Qcxt job preprocess failed. Exception occurred")
        finally:
            logger.info("finish qcxt preprocess")

    def ReqQcxtPostProcess(  # type: ignore[override]  # ruff: ignore[invalid-function-name]
        self,
        request: ReqQcxtPostProcessRequest,
        context: grpc.ServicerContext,  # ruff: ignore[unused-method-argument]
    ) -> ReqQcxtPostProcessResponse:
        """Handle gRPC request for postprocessing estimation job.

        This method returns the expected value and standard deviation calculated
        from the given sub-expectation values, sub-standard deviations and
        coefficients.

        Args:
            request: The gRPC request containing the sub_expvals, sub_stds and
                coefficients for estimation job.
            context: The gRPC context for the request.

        Returns:
            The gRPC response containing the expval and stds calculated for
            estimation job.

        """
        sub_expvals = request.sub_expvals
        sub_stds = request.sub_stds
        coefficients = request.coefficients
        try:
            logger.info("start qcxt postprocess")
            logger.debug(
                "sub_expvals:%s, sub_stds:%s, coefficients:%s",
                sub_expvals,
                sub_stds,
                coefficients,
            )
            exp_val, stds = self._postprocess(sub_expvals, sub_stds, coefficients)
            logger.debug(
                "expval:%f, stds:%f",
                exp_val,
                stds,
            )
            return ReqQcxtPostProcessResponse(exp_val=exp_val, stds=stds)
        except Exception:
            logger.exception("Qcxt job postprocess failed. Exception occurred")
        finally:
            logger.info("finish qcxt postprocess")

    def _preprocess(
        self,
        program: str,
        device_data: DeviceData,
        shots: int,
        basis_gates: list[str],
    ) -> tuple[list[str], list[float], list[dict[str, Any]]]:
        qc: QuantumCircuit = qasm3.loads(program)
        gate_counts = qc.count_ops()
        logger.debug(
            "input QASM code is successfully transformed to QuantumCircuit. "
            "Stats: qubits=%d, clbits=%d, CX_gate_count=%d",
            qc.num_qubits,
            qc.num_clbits,
            gate_counts.get("cx", 0),
        )

        # Select cuts based on the device data and circuit
        selector = CutSelector(
            device_data,
            max_cuts=self.max_cuts,
            cut_fidelity_threshold=self.fidelity_threshold,
            min_partition_ratio=self.min_partition_ratio,
        )
        qc_graph = selector.circuit_to_graph(qc)
        cut_pairs = selector.solve(qc_graph)

        # If no cuts are selected, return the original circuit without cuts
        if not cut_pairs:
            logger.debug(
                "No cuts selected. Returning the original circuit without cuts."
            )
            return [program], [1.0], []

        logger.debug(
            "Selected cuts as candidates: %s",
            [
                {
                    "control": cut.control,
                    "target": cut.target,
                    "fidelity": cut.fidelity,
                    "instruction_index": cut.instruction_index,
                }
                for cut in cut_pairs
            ],
        )

        # Generate sub-circuits and coefficients based on the selected cuts
        sub_circuits, coefficients, cut_cx_indices = _cut(qc, cut_pairs, basis_gates)
        logger.debug(
            "Generated %d sub-circuits by applying the selected cuts as candidates. ",
            len(sub_circuits),
        )

        # Decide whether the selected cut actually reduces the noise-induced error
        simulator = Simulator(device_data)
        expval_ideal = simulator.calculate_expval([qc], shots, is_noisy_simulated=False)
        expval_std = simulator.calculate_expval([qc], shots)
        expval_cut = simulator.calculate_expval(sub_circuits, shots, coefficients)
        logger.debug(
            "Simulation results: expval_ideal=%f, expval_std=%f, expval_cut=%f",
            expval_ideal,
            expval_std,
            expval_cut,
        )

        # Apply the cut only if it reduces the noise-induced error
        if simulator.judge(
            expval_ideal=expval_ideal, expval_std=expval_std, expval_cut=expval_cut
        ):
            logger.debug(
                "The selected cuts will reduce the noise-induced error. "
                "Proceeding with the cut circuits."
            )
        else:
            # Fall back to the non-cut circuit
            cut_pairs = []
            cut_cx_indices = []
            sub_circuits, coefficients = [qc], [1.0]
            logger.debug(
                "The selected cuts will not reduce the noise-induced error. "
                "Falling back to the original circuit without cuts."
            )

        # Prepare the cut information to be sent back to the client
        sub_programs = [qasm3.dumps(sub_qc) for sub_qc in sub_circuits]
        selected_cuts = [
            {
                "control": cut.control,
                "target": cut.target,
                "fidelity": cut.fidelity,
                "cx_index": cx_index,
            }
            for cut, cx_index in zip(cut_pairs, cut_cx_indices, strict=True)
        ]

        return sub_programs, coefficients, selected_cuts

    def _postprocess(  # ruff: ignore[no-self-use]
        self,
        sub_expvals: list[float],
        sub_stds: list[float],
        coefficients: list[float],
    ) -> tuple[np.float64, np.float64]:
        coeffs = np.asarray(coefficients, dtype=np.float64)
        expvals = np.asarray(sub_expvals, dtype=np.float64)
        stds = np.asarray(sub_stds, dtype=np.float64)
        exp_val = np.dot(coeffs, expvals)
        std = np.dot(np.abs(coeffs), stds)
        return exp_val, std
