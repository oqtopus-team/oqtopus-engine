import argparse
import json
import logging
import logging.config
import os
import typing
from concurrent import futures
from pathlib import Path
from statistics import mean

import grpc
import numpy as np
import yaml
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from mitiq.zne.inference import LinearFactory, PolyFactory, RichardsonFactory
from mitiq.zne.scaling import fold_gates_at_random, fold_global
from qiskit import qasm3, transpile
from qiskit.circuit import QuantumCircuit
from qiskit.circuit.quantumcircuitdata import CircuitInstruction
from qiskit.primitives.backend_estimator import (  # noqa: PLC2701
    _pauli_expval_with_variance,
)
from qiskit.quantum_info import PauliList
from qiskit.result import Counts, LocalReadoutMitigator, ProbDistribution

from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2,
    mitigator_pb2_grpc,
)

logger = logging.getLogger("oqtopus_engine_mitigator")

SUPPORTED_ZNE_FACTORIES = {"richardson", "linear", "poly"}
SUPPORTED_ZNE_FOLDINGS = {"global", "random"}
DEFAULT_BASIS_GATES = ["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the gRPC server with configuration files."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config/config.yaml",
        help="Path to the server configuration file (YAML format).",
    )
    parser.add_argument(
        "-l",
        "--logging",
        type=str,
        default="config/logging.yaml",
        help="Path to the logging configuration file (YAML format).",
    )
    return parser.parse_args()


def assign_environ(config: dict) -> dict:
    """Expand environment variables and `~` recursively in config values.

    Args:
        config: Configuration dictionary loaded from YAML.

    Returns:
        The same dictionary object with expanded string values.
    """
    for key, value in config.items():
        if type(value) is dict:
            config[key] = assign_environ(value)
        elif type(value) is str:
            tmp_value = str(os.path.expandvars(value))
            config[key] = os.path.expanduser(tmp_value)  # noqa: PTH111
    return config


def _normalize_basis_gates(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return list(DEFAULT_BASIS_GATES)
        if text.startswith("${") and text.endswith("}"):
            return list(DEFAULT_BASIS_GATES)
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return [item.strip() for item in text.split(",") if item.strip()]
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
    return list(DEFAULT_BASIS_GATES)


class ErrorMitigator(mitigator_pb2_grpc.MitigatorService):
    """gRPC service implementation for readout mitigation and ZNE pre/post processing."""

    def __init__(
        self,
        zne_default_config: dict | None = None,
    ) -> None:
        self._zne_default_config = {
            "enabled": False,
            "scale_factors": [1.0, 2.0, 3.0],
            "factory": "richardson",
            "folding": "global",
            "num_to_average": 1,
            "fail_open": True,
            "poly_order": 2,
            "basis_gates": list(DEFAULT_BASIS_GATES),
        }
        if zne_default_config:
            self._zne_default_config.update(zne_default_config)
        self._zne_default_config["basis_gates"] = _normalize_basis_gates(
            self._zne_default_config.get("basis_gates")
        )
        logger.info(
            "ErrorMitigator was initialized",
            extra={
                "zne_default_config": self._zne_default_config,
            },
        )

    def ReqMitigation(  # noqa: N802
        self,
        request: mitigator_pb2.ReqMitigationRequest,
        context: grpc.ServicerContext,
    ) -> mitigator_pb2.ReqMitigationResponse:
        """Handle readout-error mitigation RPC request.

        Args:
            request: Mitigation request including device topology, counts and program.
            context: gRPC service context.

        Returns:
            Mitigated counts response, or an empty response on internal error.
        """
        try:
            logger.info("start ro_error_mitigation process")
            mitigated_counts = self.ro_error_mitigation(
                request.device_topology,
                request.counts,
                request.program,
            )
            logger.debug("mitigated_counts=%s", mitigated_counts)
            return mitigator_pb2.ReqMitigationResponse(counts=mitigated_counts)
        except Exception as e:
            logger.exception("mitigation process failed")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return mitigator_pb2.ReqMitigationResponse()
        finally:
            logger.info("finish ro_error_mitigation process")

    def ReqZneMitigation(  # noqa: N802
        self,
        request: mitigator_pb2.ReqZneMitigationRequest,
        context: grpc.ServicerContext,
    ) -> mitigator_pb2.ReqZneMitigationResponse:
        """Deprecated monolithic RPC. Use ReqZnePreProcess/ReqZnePostProcess."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details(
            "ReqZneMitigation is deprecated. Use ReqZnePreProcess and ReqZnePostProcess."
        )
        return mitigator_pb2.ReqZneMitigationResponse()

    def ReqZnePreProcess(  # noqa: N802
        self,
        request: mitigator_pb2.ReqZnePreProcessRequest,
        context: grpc.ServicerContext,
    ) -> mitigator_pb2.ReqZnePreProcessResponse:
        try:
            zne_config = self._parse_zne_config(request.zne_config_json)
            logger.info(
                "start zne pre-process",
                extra={
                    "job_id": request.job_id,
                    "scale_factors": zne_config["scale_factors"],
                    "factory": zne_config["factory"],
                    "folding": zne_config["folding"],
                    "num_to_average": zne_config["num_to_average"],
                },
            )
            execution_programs = self._build_execution_programs(
                programs=list(request.programs), zne_config=zne_config
            )
            metadata = {
                "factory": zne_config["factory"],
                "folding": zne_config["folding"],
                "num_to_average": zne_config["num_to_average"],
                "scale_factors": zne_config["scale_factors"],
            }
            return mitigator_pb2.ReqZnePreProcessResponse(
                execution_programs=execution_programs,
                metadata_json=json.dumps(metadata),
            )
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return mitigator_pb2.ReqZnePreProcessResponse()
        finally:
            logger.info("finish zne pre-process")

    def ReqZnePostProcess(  # noqa: N802
        self,
        request: mitigator_pb2.ReqZnePostProcessRequest,
        context: grpc.ServicerContext,
    ) -> mitigator_pb2.ReqZnePostProcessResponse:
        try:
            zne_config = self._parse_zne_config(request.zne_config_json)
            logger.info("start zne post-process")
            exp_value, stds, metadata = self._zne_postprocess(request, zne_config)
            return mitigator_pb2.ReqZnePostProcessResponse(
                exp_value=exp_value,
                stds=stds,
                metadata_json=json.dumps(metadata),
            )
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return mitigator_pb2.ReqZnePostProcessResponse()
        finally:
            logger.info("finish zne post-process")

    def ro_error_mitigation(
        self,
        device_topology: mitigator_pb2.DeviceTopology,
        counts: dict[str, int],
        program: str,
    ) -> dict[str, int]:
        assignment_matrices = []
        qubits = device_topology.qubits
        shots = sum(counts.values())
        measured_qubits = get_measured_qubits(program)
        n_qubits = len(measured_qubits)

        # LocalReadoutMitigator (used below) creates a vector of length 2^(#qubits).
        if n_qubits > 32:  # If #qubits is 32, it requires a memory of 32GB.
            raise ValueError(
                "input measured_qubits is too large, it requires a memory of over 32GB"
            )

        for qubit_id in measured_qubits:
            mes_error = qubits[qubit_id].mes_error
            amat = np.array(
                [
                    [1 - mes_error.p0m1, mes_error.p1m0],
                    [mes_error.p0m1, 1 - mes_error.p1m0],
                ],
                dtype=float,
            )
            assignment_matrices.append(amat)
        local_mitigator = LocalReadoutMitigator(assignment_matrices)
        bin_counts = {f"0b{k}": v for k, v in counts.items()}
        quasi_dist = local_mitigator.quasi_probabilities(
            Counts(bin_counts, memory_slots=n_qubits)
        )
        nearest_prob: ProbDistribution = quasi_dist.nearest_probability_distribution()  # type: ignore[assignment]
        bin_prob = nearest_prob.binary_probabilities(num_bits=n_qubits)
        mitigated_counts = {k: int(v * shots) for k, v in bin_prob.items()}
        return mitigated_counts

    def _build_execution_programs(
        self,
        programs: list[str],
        zne_config: dict,
    ) -> list[mitigator_pb2.ZneExecutionProgram]:
        result: list[mitigator_pb2.ZneExecutionProgram] = []
        for scale_factor in zne_config["scale_factors"]:
            for rep in range(zne_config["num_to_average"]):
                for index, program in enumerate(programs):
                    folded_program = self._fold_program(
                        program=program,
                        scale_factor=scale_factor,
                        folding=zne_config["folding"],
                        basis_gates=zne_config["basis_gates"],
                    )
                    result.append(
                        mitigator_pb2.ZneExecutionProgram(
                            scale_factor=scale_factor,
                            repetition=rep,
                            program_index=index,
                            suffix=f"s{scale_factor}-r{rep}-p{index}",
                            program=folded_program,
                        )
                    )
        return result

    def _zne_postprocess(
        self,
        request: mitigator_pb2.ReqZnePostProcessRequest,
        zne_config: dict,
    ) -> tuple[float, float, dict]:
        grouped: dict[tuple[float, int], dict[int, dict[str, int]]] = {}
        for item in request.execution_results:
            key = (float(item.scale_factor), int(item.repetition))
            by_index = grouped.setdefault(key, {})
            by_index[int(item.program_index)] = {k: int(v) for k, v in item.counts.items()}

        scale_results: list[dict] = []
        for scale_factor in zne_config["scale_factors"]:
            per_rep_exp_values: list[float] = []
            per_rep_stds: list[float] = []
            for rep in range(zne_config["num_to_average"]):
                key = (float(scale_factor), int(rep))
                by_index = grouped.get(key, {})
                if not by_index:
                    raise ValueError(f"missing zne execution results for scale={scale_factor}, rep={rep}")
                counts_list = [by_index[i] for i in sorted(by_index.keys())]
                exp_value, stds = self._postprocess_estimation(
                    counts_list=counts_list,
                    grouped_operators_json=request.grouped_operators_json,
                )
                per_rep_exp_values.append(exp_value)
                per_rep_stds.append(stds)
            scale_results.append(
                {
                    "scale_factor": float(scale_factor),
                    "exp_value": float(mean(per_rep_exp_values)),
                    "stds": float(mean(per_rep_stds)),
                }
            )

        exp_values = [item["exp_value"] for item in scale_results]
        stds_values = [item["stds"] for item in scale_results]
        zne_exp = self._extrapolate_to_zero_noise(
            scale_factors=zne_config["scale_factors"],
            exp_values=exp_values,
            factory_name=zne_config["factory"],
            poly_order=zne_config["poly_order"],
        )
        zne_stds = float(mean(stds_values))
        metadata = {
            "scale_results": scale_results,
            "factory": zne_config["factory"],
            "folding": zne_config["folding"],
            "num_to_average": zne_config["num_to_average"],
        }
        return zne_exp, zne_stds, metadata

    def _postprocess_estimation(
        self,
        counts_list: list[dict[str, int]],
        grouped_operators_json: str,
    ) -> tuple[float, float]:
        operators = json.loads(grouped_operators_json)
        exp_value: np.float64 | np.complex64 = np.float64(0.0)
        stds: np.float64 | np.complex64 = np.float64(0.0)

        for counts, pauli_list, coeff_list in zip(
            counts_list,
            operators[0],
            operators[1],
            strict=True,
        ):
            paulis = PauliList(pauli_list)
            coeffs = np.array(coeff_list)
            exp_values, variances = _pauli_expval_with_variance(Counts(counts), paulis)
            exp_value += np.dot(exp_values, coeffs)
            stds += np.dot(variances**0.5, np.abs(coeffs))

        shots = sum(counts_list[0].values()) if counts_list else 0
        if shots > 0:
            stds /= np.sqrt(shots)
        return float(np.real_if_close([exp_value])[0]), float(stds)

    def _fold_program(
        self,
        program: str,
        scale_factor: float,
        folding: str,
        basis_gates: list[str],
    ) -> str:
        if scale_factor == 1.0:
            return program
        circuit: QuantumCircuit = qasm3.loads(program)
        # qasm3.loads() may attach layout metadata that breaks mitiq/qiskit conversion.
        # Rebuild the circuit without layout to make folding robust for physical-qubit QASM.
        clean_circuit = QuantumCircuit(circuit.num_qubits, circuit.num_clbits)
        for instruction in circuit.data:
            qargs = [clean_circuit.qubits[circuit.find_bit(q).index] for q in instruction.qubits]
            cargs = [clean_circuit.clbits[circuit.find_bit(c).index] for c in instruction.clbits]
            clean_circuit.append(instruction.operation, qargs, cargs)

        if folding == "random":
            folded = fold_gates_at_random(clean_circuit, scale_factor=scale_factor)
        else:
            folded = fold_global(clean_circuit, scale_factor=scale_factor)
        # QPU gateway accepts a restricted gate set. Folded circuits may introduce
        # unsupported gates (e.g., "s"), so normalize to the supported basis.
        normalized = transpile(
            folded,
            basis_gates=basis_gates,
            optimization_level=0,
        )
        return qasm3.dumps(normalized)

    def _extrapolate_to_zero_noise(
        self,
        scale_factors: list[float],
        exp_values: list[float],
        factory_name: str,
        poly_order: int,
    ) -> float:
        if factory_name == "linear":
            factory = LinearFactory(scale_factors)
        elif factory_name == "poly":
            order = min(max(poly_order, 1), len(scale_factors) - 1)
            factory = PolyFactory(scale_factors, order=order)
        else:
            factory = RichardsonFactory(scale_factors)

        for sf, exp_value in zip(scale_factors, exp_values, strict=True):
            factory.push({"scale_factor": sf}, exp_value)
        return float(factory.reduce())

    def _parse_zne_config(self, zne_config_json: str) -> dict:
        req_cfg = json.loads(zne_config_json) if zne_config_json else {}
        zne_cfg = dict(self._zne_default_config)
        zne_cfg.update(req_cfg)

        scale_factors = [float(v) for v in zne_cfg["scale_factors"]]
        if len(scale_factors) < 2:
            raise ValueError("scale_factors must contain at least two values")
        if any(v <= 0.0 for v in scale_factors):
            raise ValueError("scale_factors must be positive")

        factory = str(zne_cfg["factory"]).lower()
        if factory not in SUPPORTED_ZNE_FACTORIES:
            raise ValueError(f"unsupported zne factory: {factory}")

        folding = str(zne_cfg["folding"]).lower()
        if folding not in SUPPORTED_ZNE_FOLDINGS:
            raise ValueError(f"unsupported zne folding: {folding}")

        num_to_average = int(zne_cfg["num_to_average"])
        if num_to_average < 1:
            raise ValueError("num_to_average must be >= 1")

        return {
            "enabled": bool(zne_cfg.get("enabled", False)),
            "scale_factors": scale_factors,
            "factory": factory,
            "folding": folding,
            "num_to_average": num_to_average,
            "fail_open": bool(zne_cfg.get("fail_open", True)),
            "poly_order": int(zne_cfg.get("poly_order", 2)),
            "basis_gates": _normalize_basis_gates(zne_cfg.get("basis_gates")),
        }

    def _extract_fail_open(self, zne_config_json: str) -> bool:
        try:
            req_cfg = json.loads(zne_config_json) if zne_config_json else {}
        except json.JSONDecodeError:
            return bool(self._zne_default_config.get("fail_open", True))
        return bool(req_cfg.get("fail_open", self._zne_default_config["fail_open"]))


def get_measured_qubits(program: str) -> list[int]:
    """Extract measured qubit indices from a QASM 3 program.

    Args:
        program: OpenQASM 3 source string.

    Returns:
        Measured qubit indices ordered by classical-bit index.

    Raises:
        ValueError: If QASM parsing fails or circuit bit mapping is inconsistent.
    """
    try:
        qc = qasm3.loads(program)
    except Exception as e:
        raise ValueError(f"Invalid QASM 3 program: {e}") from e

    measured_qubits_dict: dict[int, int] = {}
    for _instruction in qc.data:
        instruction = typing.cast("CircuitInstruction", _instruction)
        if instruction.operation.name == "measure":
            clbits = [clbit._index for clbit in instruction.clbits]
            qubits = []
            for qubit in instruction.qubits:
                bit_info = qc.find_bit(qubit)
                if bit_info is None:
                    raise ValueError(f"Qubit {qubit} not found in circuit bits.")
                qubits.append(bit_info.index)
            for clbit, qubit in zip(clbits, qubits, strict=False):
                measured_qubits_dict[clbit] = qubit

    return [measured_qubits_dict[k] for k in sorted(measured_qubits_dict.keys())]


def serve(config_yaml_path: str, logging_yaml_path: str) -> None:
    """Start the mitigator gRPC server.

    Args:
        config_yaml_path: Path to service config YAML.
        logging_yaml_path: Path to logging config YAML.
    """
    with Path(config_yaml_path).open("r", encoding="utf-8") as file:
        config_yaml = assign_environ(yaml.safe_load(file))
    with Path(logging_yaml_path).open("r", encoding="utf-8") as file:
        logging_yaml = assign_environ(yaml.safe_load(file))
        logging.config.dictConfig(logging_yaml)

    max_workers = int(config_yaml["proto"].get("max_workers") or 10)
    address = str(config_yaml["proto"].get("address") or "[::]:52011")
    zne_default_config = config_yaml.get("zne", {}).get("default_config", {})

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    mitigator_pb2_grpc.add_MitigatorServiceServicer_to_server(
        ErrorMitigator(
            zne_default_config=zne_default_config,
        ),
        server,
    )
    service_names = (
        mitigator_pb2.DESCRIPTOR.services_by_name["MitigatorService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    server.add_insecure_port(address)
    logger.info("Server is running on %s. max_workers=%d", address, max_workers)
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    args = _parse_args()
    serve(args.config, args.logging)
