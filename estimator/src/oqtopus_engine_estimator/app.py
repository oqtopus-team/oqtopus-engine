import argparse
import ast
import json
import logging
import re
from concurrent import futures

import grpc  # type: ignore[import-untyped]
import numpy as np
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from opentelemetry import trace
from oqtopus_util.config import load_config, setup_logging
from qiskit import QuantumCircuit, qasm3  # type: ignore[import-untyped]
from qiskit.exceptions import QiskitError  # type: ignore[import-untyped]
from qiskit.primitives import (  # type: ignore[import-untyped]
    BackendEstimatorV2 as BackendEstimator,
)
from qiskit.primitives.backend_estimator import (  # type: ignore[import-untyped]
    _pauli_expval_with_variance,  # noqa: PLC2701
)
from qiskit.primitives.containers.estimator_pub import (  # type: ignore[import-untyped]
    EstimatorPub,
)
from qiskit.providers.fake_provider import (  # type: ignore[import-untyped]
    GenericBackendV2,
)
from qiskit.quantum_info import (  # type: ignore[import-untyped]
    PauliList,
    SparsePauliOp,
)
from qiskit.result import Counts  # type: ignore[import-untyped]

from oqtopus_engine_core.interfaces.estimator_interface.v1 import (
    estimator_pb2_grpc,
)
from oqtopus_engine_core.interfaces.estimator_interface.v1.estimator_pb2 import (  # type: ignore[attr-defined]
    DESCRIPTOR,
    ReqEstimationPostProcessFromExpectationValuesRequest,
    ReqEstimationPostProcessFromExpectationValuesResponse,
    ReqEstimationPostProcessRequest,
    ReqEstimationPostProcessResponse,
    ReqEstimationPreProcessRequest,
    ReqEstimationPreProcessResponse,
)
from oqtopus_engine_estimator.observability import setup_observability

logger = logging.getLogger("oqtopus_engine_estimator")
tracer = trace.get_tracer("oqtopus_engine_estimator")


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


class ParameterValueError(ValueError):
    """Custom exception for parameter value errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)

    @property
    def message(self) -> str:
        """Get the error message."""
        return self.args[0]


class Estimator(estimator_pb2_grpc.EstimatorServiceServicer):
    """Estimator service implementation for gRPC."""

    def ReqEstimationPreProcess(  # noqa: N802
        self,
        request: ReqEstimationPreProcessRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> ReqEstimationPreProcessResponse:
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
        with tracer.start_as_current_span("estimator.ReqEstimationPreProcess") as span:
            try:
                logger.info("start estimation preprocess")
                logger.debug(
                    "Estimation preprocess request",
                    extra={
                        "qasm_code": request.qasm_code,
                        "operators": request.operators,
                        "basis_gates": request.basis_gates,
                        "mapping_list": request.mapping_list,
                    },
                )
                qasm_code = request.qasm_code
                operators = request.operators
                basis_gates = request.basis_gates
                mapping_list = request.mapping_list

                preprocessed_qasm_codes, grouped_operators = self._preprocess(
                    qasm_code, operators, basis_gates, mapping_list
                )
                return ReqEstimationPreProcessResponse(
                    qasm_codes=preprocessed_qasm_codes,
                    grouped_operators=grouped_operators,
                )
            except Exception as e:
                logger.exception("Estimation job preprocess failed. Exception occurred")
                if span.is_recording():
                    span.set_status(trace.StatusCode.ERROR, str(e))
            finally:
                logger.info("finish estimation preprocess")

    def ReqEstimationPostProcess(  # noqa: N802
        self,
        request: ReqEstimationPostProcessRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> ReqEstimationPostProcessResponse:
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
        with tracer.start_as_current_span("estimator.ReqEstimationPostProcess") as span:
            try:
                logger.info("start estimation postprocess")
                logger.debug(
                    "Estimation post-process request",
                    extra={
                        "counts": request.counts,
                        "grouped_operators": request.grouped_operators,
                    },
                )
                counts_list = request.counts
                grouped_operators = request.grouped_operators
                expval, stds = self._postprocess(counts_list, grouped_operators)
                logger.debug(
                    "Estimation post-process result",
                    extra={"expectation_value": expval, "standard_deviation": stds},
                )
                return ReqEstimationPostProcessResponse(expval=expval, stds=stds)
            except Exception as e:
                logger.exception(
                    "Estimation job postprocess failed. Exception occurred"
                )
                if span.is_recording():
                    span.set_status(trace.StatusCode.ERROR, str(e))
            finally:
                logger.info("finish estimation postprocess")

    def ReqEstimationPostProcessFromExpectationValues(  # noqa: N802
        self,
        request: ReqEstimationPostProcessFromExpectationValuesRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> ReqEstimationPostProcessFromExpectationValuesResponse:
        """Aggregate precomputed expectation values into an estimation result.

        Returns:
            The gRPC response containing the aggregated value and uncertainty.

        """
        with tracer.start_as_current_span(
            "estimator.ReqEstimationPostProcessFromExpectationValues"
        ) as span:
            try:
                logger.info("start estimation postprocess from expectation values")
                logger.debug(
                    "Expectation-value post-process request",
                    extra={
                        "expectation_value_groups": request.expectation_value_groups,
                        "grouped_operators": request.grouped_operators,
                    },
                )
                expval, stds = self._postprocess_from_expectation_values(
                    request.expectation_value_groups,
                    request.grouped_operators,
                )
                logger.debug(
                    "Expectation-value post-process result",
                    extra={
                        "expectation_value": expval,
                        "standard_deviation_upper_bound": stds,
                    },
                )
                return ReqEstimationPostProcessFromExpectationValuesResponse(
                    expectation_value=expval,
                    standard_deviation_upper_bound=stds,
                )
            except Exception as e:
                logger.exception(
                    "Estimation job postprocess from expectation values failed. "
                    "Exception occurred"
                )
                if span.is_recording():
                    span.set_status(trace.StatusCode.ERROR, str(e))
            finally:
                logger.info("finish estimation postprocess from expectation values")

    def _preprocess(  # noqa: PLR6301, PLR0914
        self,
        qasm_code: str,
        operators: str,
        basis_gates: list[str],
        mapping_list: list[int],
    ) -> tuple[list[str], str]:
        with tracer.start_as_current_span("estimator._preprocess.qasm_parse") as span:
            qc: QuantumCircuit = qasm3.loads(qasm_code)
            qc.remove_final_measurements()
            gate_counts = qc.count_ops()
            if span.is_recording():
                span.set_attribute("estimator.circuit.num_qubits", qc.num_qubits)
                span.set_attribute("estimator.circuit.num_clbits", qc.num_clbits)
                span.set_attribute("estimator.circuit.depth", qc.depth())
                span.set_attribute(
                    "estimator.circuit.gate_count", sum(gate_counts.values())
                )
            logger.debug(
                "Input QASM transformed to QuantumCircuit",
                extra={
                    "qubits": qc.num_qubits,
                    "clbits": qc.num_clbits,
                    "depth": qc.depth(),
                    "total_gates": sum(gate_counts.values()),
                    "gate_counts": gate_counts,
                },
            )

        with tracer.start_as_current_span("estimator._preprocess.operator") as span:
            op = create_qiskit_operator(operators, qc.num_qubits)
            if span.is_recording():
                span.set_attribute("estimator.operator.num_terms", len(op))
            logger.debug(
                "Input operator transformed to SparsePauliOp",
                extra={"operator": op},
            )
            if len(mapping_list) == 0:
                mapping_list = list(range(qc.num_qubits))
            elif len(mapping_list) != qc.num_qubits:
                full_indices = list(range(max(mapping_list) + 1))
                missing_list = list(set(full_indices) - set(mapping_list))
                mapping_list = list(mapping_list) + missing_list
            mapped_observable = op.apply_layout(mapping_list, num_qubits=qc.num_qubits)
            logger.debug(
                "Qubit mapping applied to observable",
                extra={"mapped_observable": mapped_observable},
            )

        with tracer.start_as_current_span(
            "estimator._preprocess.qiskit_preprocess"
        ) as span:
            backend = GenericBackendV2(
                num_qubits=qc.num_qubits, basis_gates=basis_gates
            )
            estimator = BackendEstimator(backend=backend)
            pub = (qc, mapped_observable)
            coerced_pub = EstimatorPub.coerce(pub)
            preprocessed_data = estimator._preprocess_pub(coerced_pub)  # noqa: SLF001
            preprocessed_qasm = [qasm3.dumps(qc) for qc in preprocessed_data.circuits]
            if span.is_recording():
                span.set_attribute(
                    "estimator.num_measurement_groups",
                    len(preprocessed_data.circuits),
                )
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
        logger.debug(
            "Estimation preprocess result",
            extra={
                "qasm_codes": preprocessed_qasm,
                "grouped_operators": grouped_operators,
            },
        )

        return preprocessed_qasm, grouped_operators

    def _postprocess(  # noqa: PLR6301
        self,
        counts_list: list,
        grouped_operators: str,
    ) -> tuple[np.float64 | np.complex64, np.float64 | np.complex64]:
        with tracer.start_as_current_span("estimator._postprocess.compute") as span:
            exp_value: np.float64 | np.complex64 = np.float64(0.0)
            stds: np.float64 | np.complex64 = np.float64(0.0)

            operators = json.loads(grouped_operators)
            if span.is_recording():
                span.set_attribute("estimator.num_measurement_groups", len(counts_list))
                if counts_list:
                    span.set_attribute(
                        "estimator.shots",
                        sum(counts_list[0].counts.values()),
                    )

            for counts, pauli_list, coeff_list in zip(
                counts_list,
                operators[0],
                operators[1],
                strict=True,
            ):
                coeffs = np.array(coeff_list)
                paulis = PauliList(pauli_list)
                exp_values, variances = _pauli_expval_with_variance(
                    Counts(counts.counts), paulis
                )
                exp_value += np.dot(exp_values, coeffs)
                shots = sum(counts.counts.values())
                stds += np.dot(variances**0.5, np.abs(coeffs)) / np.sqrt(shots)

            return np.real_if_close([exp_value])[0], stds

    def _postprocess_from_expectation_values(  # noqa: PLR6301
        self,
        expectation_value_groups: list,
        grouped_operators: str,
    ) -> tuple[np.float64 | np.complex64, np.float64 | np.complex64]:
        exp_value: np.float64 | np.complex64 = np.float64(0.0)
        stds: np.float64 | np.complex64 = np.float64(0.0)

        operators = json.loads(grouped_operators)
        for expectation_values, pauli_list, coeff_list in zip(
            expectation_value_groups,
            operators[0],
            operators[1],
            strict=True,
        ):
            values = np.array(expectation_values.values)
            standard_deviation_upper_bounds = np.array(
                expectation_values.standard_deviation_upper_bounds
            )
            coeffs = np.array(coeff_list)
            if not (
                values.size
                == standard_deviation_upper_bounds.size
                == coeffs.size
                == len(pauli_list)
            ):
                message = (
                    "Expectation values, standard-deviation upper bounds, Pauli "
                    "labels, and operator coefficients must have equal lengths"
                )
                raise ValueError(message)
            exp_value += np.dot(values, coeffs)
            stds += np.dot(standard_deviation_upper_bounds, np.abs(coeffs))

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
    config_yaml = load_config(config_yaml_path)
    logging_yaml = load_config(logging_yaml_path)
    setup_logging(logging_yaml)

    setup_observability(config_yaml)

    max_workers = int(config_yaml["proto"].get("max_workers") or 10)
    address = str(config_yaml["proto"].get("address") or "[::]:51012")

    # create the gRPC server
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=config_yaml["proto"]["grpc_options"],
    )
    estimator_pb2_grpc.add_EstimatorServiceServicer_to_server(Estimator(), server)

    service_names = (
        DESCRIPTOR.services_by_name["EstimatorService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    server.add_insecure_port(address)
    logger.info(
        "Server is running",
        extra={"address": address, "max_workers": max_workers},
    )

    # start the server
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    args = _parse_args()
    serve(args.config, args.logging)
