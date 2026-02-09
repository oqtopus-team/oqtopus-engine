import argparse
import ast
import json
import logging
import logging.config
import os
import re
from concurrent import futures
from pathlib import Path

import grpc
import numpy as np
import yaml
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from qiskit import QuantumCircuit, qasm3
from qiskit.exceptions import QiskitError
from qiskit.primitives import BackendEstimatorV2 as BackendEstimator
from qiskit.primitives.backend_estimator import (
    _pauli_expval_with_variance,  # noqa: PLC2701
)
from qiskit.primitives.containers.estimator_pub import EstimatorPub
from qiskit.providers.fake_provider import GenericBackendV2
from qiskit.quantum_info import PauliList, SparsePauliOp
from qiskit.result import Counts

from oqtopus_engine_core.interfaces.estimator_interface.v1 import (
    estimator_pb2,
    estimator_pb2_grpc,
)

logger = logging.getLogger("oqtopus_engine_estimator")


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
    """Expand environment variables and the user directory "~" in the values of `dict`.

    Args:
        config (dict): `dict` that expands environment variables
            and the user directory "~" in its values.

    Returns:
        dict: expanded `dict`.

    """
    for key, value in config.items():
        if type(value) is dict:
            config[key] = assign_environ(value)
        elif type(value) is str:
            tmp_value = str(os.path.expandvars(value))
            config[key] = os.path.expanduser(tmp_value)  # noqa: PTH111
    return config


class ParameterValueError(ValueError):
    """Custom exception for parameter value errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)

    @property
    def message(self) -> str:
        """Get the error message."""
        return self.args[0]


# response
class Estimator(estimator_pb2_grpc.EstimatorService):
    """Estimator service implementation for gRPC."""

    def ReqEstimationPreProcess(  # noqa: N802
        self,
        request: estimator_pb2.ReqEstimationPreProcessRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> estimator_pb2.ReqEstimationPreProcessResponse:
        """Handle gRPC request for preprocessing estimation job.

        This method returns QASM codes with an operator based measurement
        operator added to the given QASM code.

        Args:
            request: The gRPC request containing the QASM codes, operators,
                basis_gates and mapping_list for estimation job.
            context: The gRPC context for the request.

        Returns:
            The gRPC response containing the preprocessed_qasm_codes,
            grouped_operators for estimation job.

        """
        try:
            logger.info("start estimation preprocess")
            logger.debug(
                "qasm_code:%s, operators:%s, basis_gates:%s, mapping_list:%s",
                request.qasm_code,
                request.operators,
                request.basis_gates,
                request.mapping_list,
            )
            qasm_code = request.qasm_code
            operators = request.operators
            basis_gates = request.basis_gates
            mapping_list = request.mapping_list

            preprocessed_qasm_codes, grouped_operators = self._preprocess(
                qasm_code, operators, basis_gates, mapping_list
            )
            return estimator_pb2.ReqEstimationPreProcessResponse(
                qasm_codes=preprocessed_qasm_codes, grouped_operators=grouped_operators
            )
        except Exception:
            logger.exception("Estimation job preprocess failed. Exception occurred")
        finally:
            logger.info("finish estimation preprocess")

    def ReqEstimationPostProcess(  # noqa: N802
        self,
        request: estimator_pb2.ReqEstimationPostProcessRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> estimator_pb2.ReqEstimationPostProcessResponse:
        """Handle gRPC request for postprocessing estimation job.

        This method returns the expected value and standard deviation from the
        given Counts and operator.

        Args:
            request: The gRPC request containing the counts_list and
                grouped_operators for estimation job.
            context: The gRPC context for the request.

        Returns:
            The gRPC response containing the expval and stds calculated for
            estimation job.

        """
        try:
            logger.info("start estimation postprocess")
            logger.debug(
                "counts:%s, grouped_operators:%s",
                request.counts,
                request.grouped_operators,
            )
            counts_list = request.counts
            grouped_operators = request.grouped_operators
            expval, stds = self._postprocess(counts_list, grouped_operators)
            logger.debug(
                "expval:%f, stds:%f",
                expval,
                stds,
            )
            return estimator_pb2.ReqEstimationPostProcessResponse(
                expval=expval, stds=stds
            )
        except Exception:
            logger.exception("Estimation job postprocess failed. Exception occurred")
        finally:
            logger.info("finish estimation postprocess")

    def _preprocess(  # noqa: PLR6301, PLR0914
        self,
        qasm_code: str,
        operators: str,
        basis_gates: list[str],
        mapping_list: list[int],
    ) -> tuple[list[str], str]:
        qc: QuantumCircuit = qasm3.loads(qasm_code)
        qc.remove_final_measurements()
        gate_counts = qc.count_ops()
        logger.debug(
            "input QASM code is successfully transformed to QuantumCircuit. "
            "Stats: qubits=%d, clbits=%d, depth=%d, total_gates=%d, gate_counts=%s",
            qc.num_qubits,
            qc.num_clbits,
            qc.depth(),
            sum(gate_counts.values()),
            gate_counts,
        )

        op = create_qiskit_operator(operators, qc.num_qubits)
        logger.debug(
            "input operator is successfully transformed to SparsePauliOp %s.", op
        )
        if len(mapping_list) == 0:
            mapping_list = list(range(qc.num_qubits))
        elif len(mapping_list) != qc.num_qubits:
            full_indices = list(range(max(mapping_list) + 1))
            missing_list = list(set(full_indices) - set(mapping_list))
            mapping_list = list(mapping_list) + missing_list
        mapped_observable = op.apply_layout(mapping_list, num_qubits=qc.num_qubits)
        logger.debug(
            "input mapping_list is successfully applied to observable %s.",
            mapped_observable,
        )

        backend = GenericBackendV2(num_qubits=qc.num_qubits, basis_gates=basis_gates)
        estimator = BackendEstimator(backend=backend)
        pub = (qc, mapped_observable)
        coerced_pub = EstimatorPub.coerce(pub)
        preprocessed_data = estimator._preprocess_pub(coerced_pub)  # noqa: SLF001
        preprocessed_qasm = [qasm3.dumps(qc) for qc in preprocessed_data.circuits]
        pauli_coeff_map = dict(preprocessed_data.observables.tolist())
        grouped_meas_paulis = [
            qc.metadata["meas_paulis"].to_labels() for qc in preprocessed_data.circuits
        ]
        grouped_orig_paulis = [
            qc.metadata["orig_paulis"].to_labels() for qc in preprocessed_data.circuits
        ]
        grouped_coeffs = [
            [pauli_coeff_map[pauli] for pauli in pauli_list]
            for pauli_list in grouped_orig_paulis
        ]
        grouped_operators = json.dumps([grouped_meas_paulis, grouped_coeffs])
        logger.debug("qasms:%s, operators:%s", preprocessed_qasm, grouped_operators)

        return preprocessed_qasm, grouped_operators

    def _postprocess(  # noqa: PLR6301
        self,
        counts_list: list,
        grouped_operators: str,
    ) -> tuple[np.float64 | np.complex64, np.float64 | np.complex64]:
        exp_value: np.float64 | np.complex64 = np.float64(0.0)
        stds: np.float64 | np.complex64 = np.float64(0.0)

        operators = json.loads(grouped_operators)
        for counts, pauli_list, coeff_list in zip(
            counts_list, operators[0], operators[1], strict=True
        ):
            paulis = PauliList(pauli_list)
            coeffs = np.array(coeff_list)
            exp_values, variances = _pauli_expval_with_variance(
                Counts(counts.counts), paulis
            )
            exp_value += np.dot(exp_values, coeffs)
            stds += np.dot(variances**0.5, np.abs(coeffs))
        shots = sum(counts_list[0].counts.values())
        stds /= np.sqrt(shots)

        return np.real_if_close([exp_value])[0], stds


def create_qiskit_operator(op_string: str, n_qubits: int) -> SparsePauliOp:
    """Create a Qiskit SparsePauliOp from a string representation.

    Args:
        op_string: Pauli labels and indices. It is the 'operator' value in
            the Web API response.
        n_qubits: The qubit size of the quantum circuit.

    Returns:
        SparsePauliOp representing the operator.

    Raises:
        ParameterValueError: If the specified operator is invalid.

    """
    # Parse op_params
    op_params = ast.literal_eval(op_string)

    # There is no need to validate the value of op_params
    # because it has already been validated in the cloud.
    pauli_terms = []
    for op_param in op_params:
        # insert a space between label and index
        # Handle cases like "X 0X 1" or "X0X1" -> "X 0 X 1"
        # First remove all spaces, then add spaces between pauli and index
        pauli_and_index_str: str = re.sub(
            r"([IXYZ])(\d+)", r"\1 \2 ", op_param[0].replace(" ", "")
        ).strip()

        # I-label can be used with no index;
        # it can appear as an independent term.
        if pauli_and_index_str == "I":
            # Complement an index;
            # Via SparsePauliOp.from_sparse_list(...),
            # this is interpreted as 'I 0 I 1 I 2 ...'
            pauli_and_index_str = "I 0"

        pauli_and_index_list = pauli_and_index_str.split()  # e.g., ['X', '0', 'Z', '1']
        pauli_label_str = "".join(pauli_and_index_list[0::2])  # e.g., 'XZ'
        pauli_indices = [
            int(index_str) for index_str in pauli_and_index_list[1::2]
        ]  # e.g., [0, 1]
        coef = complex(op_param[1], 0.0)
        pauli_terms.append((pauli_label_str, pauli_indices, coef))

    try:
        return SparsePauliOp.from_sparse_list(pauli_terms, num_qubits=n_qubits)
    except QiskitError as e:
        msg = f"The specified operator is invalid. {e.args[0]}"
        raise ParameterValueError(msg) from e


def serve(config_yaml_path: str, logging_yaml_path: str) -> None:
    """Start the gRPC server with the specified configuration and logging settings.

    This function initializes and starts a gRPC server using the configuration
    provided in the YAML files for the server and logging. It sets up a
    transpiler service, configures the server's address and worker threads,
    and waits for the server to terminate.

    Args:
        config_yaml_path (str): Path to the YAML file containing the server's
            configuration. The file should define `proto.max_workers` and
            `proto.address` settings.
        logging_yaml_path (str): Path to the YAML file containing logging configuration.

    """
    # load config
    with Path(config_yaml_path).open("r", encoding="utf-8") as file:
        config_yaml = assign_environ(yaml.safe_load(file))
    with Path(logging_yaml_path).open("r", encoding="utf-8") as file:
        logging_yaml = assign_environ(yaml.safe_load(file))
        logging.config.dictConfig(logging_yaml)

    max_workers = int(config_yaml["proto"].get("max_workers") or 10)
    address = str(config_yaml["proto"].get("address") or "[::]:52012")

    # create the gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    estimator_pb2_grpc.add_EstimatorServiceServicer_to_server(Estimator(), server)
    service_names = (
        estimator_pb2.DESCRIPTOR.services_by_name["EstimatorService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    server.add_insecure_port(address)
    logger.info("Server is running on %s. max_workers=%d", address, max_workers)

    # start the server
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    args = _parse_args()
    serve(args.config, args.logging)
