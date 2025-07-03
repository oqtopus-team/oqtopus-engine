import ast
import datetime
import json
import logging
import os
import re
from concurrent import futures
from logging.handlers import TimedRotatingFileHandler

import estimation_interface.v1.estimator_pb2 as estimator_pb2
import estimation_interface.v1.estimator_pb2_grpc as estimator_pb2_grpc
import grpc
import numpy as np
from qiskit import QuantumCircuit, qasm3
from qiskit.exceptions import QiskitError
from qiskit.primitives import BackendEstimatorV2 as BackendEstimator
from qiskit.primitives.backend_estimator import _pauli_expval_with_variance
from qiskit.primitives.containers.estimator_pub import EstimatorPub
from qiskit.providers.fake_provider import GenericBackendV2
from qiskit.quantum_info import PauliList, SparsePauliOp
from qiskit.result import Counts

# port number for gRPC server
PORT_NUM = os.getenv("ESTIMATOR_PORT", 5003)
MAX_THREADS = os.getenv("ESTIMATOR_WORKERS", 0)


class ParameterValueError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)

    @property
    def message(self):
        return self.args[0]


# response
class Estimator(estimator_pb2_grpc.EstimationJobService):
    def __init__(self, logger):
        self.logger = logger

    def ReqEstimationPreProcess(self, request, context):
        """Handle gRPC request for preprocessing estimation job.

           This method returns QASM codes with an operator based measurement operator
           added to the given QASM code.

        Args:
            request (estimator_pb2_grpc.request): The gRPC request containing the
                QASM codes, operators, basis_gates and mapping_list for estimation job.
            context (grpc.ServicerContext): The gRPC context for the request.
        Returns:
            estimator_pb2.ReqEstimationPreProcessResponse: The gRPC response containing the
                preprocessed_qasm_codes, grouped_operators for estimation job.
        """
        try:
            self.logger.info("start estimation preprocess")
            self.logger.debug(
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
        except Exception as e:
            self.logger.exception(
                f"Estimation job preprocess failed. Exception occurred:{e}"
            )
        finally:
            self.logger.info("finish estimation preprocess")

    def ReqEstimationPostProcess(self, request, context):
        """Handle gRPC request for postprocessing estimation job.

           This method returns the expected value and standard deviation from the given Counts and operator.

        Args:
            request (estimator_pb2_grpc.request): The gRPC request containing the
                counts_list and grouped_operators for estimation job.
            context (grpc.ServicerContext): The gRPC context for the request.
        Returns:
            estimator_pb2.ReqEstimationPostProcessResponse: The gRPC response containing the
                expval and stds calculated for estimation job.
        """
        try:
            self.logger.info("start estimation postprocess")
            self.logger.debug(
                "counts:%s, grouped_operators:%s",
                request.counts,
                request.grouped_operators,
            )
            counts_list = request.counts
            grouped_operators = request.grouped_operators
            expval, stds = self._postprocess(counts_list, grouped_operators)
            self.logger.debug(
                "expval:%f, stds:%f",
                expval,
                stds,
            )
            return estimator_pb2.ReqEstimationPostProcessResponse(
                expval=expval, stds=stds
            )
        except Exception as e:
            self.logger.exception(
                f"Estimation job postprocess failed. Exception occurred:{e}"
            )
        finally:
            self.logger.info("finish estimation postprocess")

    def _preprocess(
        self,
        qasm_code: str,
        operators: str,
        basis_gates: list[str],
        mapping_list: list[int],
    ) -> tuple[list[str], str]:
        qc: QuantumCircuit = qasm3.loads(qasm_code)
        qc.remove_final_measurements()
        self.logger.debug(
            "input QASM code is successfully transformed to QuantumCircuit %s.", qc
        )

        op = create_qiskit_operator(operators, qc.num_qubits)
        self.logger.debug(
            "input operator is successfully transformed to SparsePauliOp %s.", op
        )
        if len(mapping_list) == 0:
            mapping_list = list(range(qc.num_qubits))
        elif len(mapping_list) != qc.num_qubits:
            full_indices = list(range(0, max(mapping_list) + 1))
            missing_list = list(set(full_indices) - set(mapping_list))
            mapping_list = list(mapping_list) + missing_list
        mapped_observable = op.apply_layout(mapping_list, num_qubits=qc.num_qubits)
        self.logger.debug(
            "input mapping_list is successfully applied to observable %s.",
            mapped_observable,
        )

        backend = GenericBackendV2(num_qubits=qc.num_qubits, basis_gates=basis_gates)
        estimator = BackendEstimator(backend=backend)
        pub = (qc, mapped_observable)
        coerced_pub = EstimatorPub.coerce(pub)
        preprocessed_data = estimator._preprocess_pub(coerced_pub)
        preprocessed_qasm = [qasm3.dumps(qc) for qc in preprocessed_data.circuits]
        pauli_coeff_map = dict(preprocessed_data.observables.tolist())
        grouped_meas_paulis = [
            qc.metadata["meas_paulis"].to_labels() for qc in preprocessed_data.circuits
        ]
        grouped_orig_paulis = [
            qc.metadata["orig_paulis"].to_labels() for qc in preprocessed_data.circuits
        ]
        grouped_coeffs = []
        for pauli_list in grouped_orig_paulis:
            grouped_coeffs.append([pauli_coeff_map[pauli] for pauli in pauli_list])
        grouped_operators = json.dumps([grouped_meas_paulis, grouped_coeffs])
        self.logger.debug(
            "qasms:%s, operators:%s", preprocessed_qasm, grouped_operators
        )

        return preprocessed_qasm, grouped_operators

    def _postprocess(
        self, counts_list, grouped_operators
    ) -> tuple[np.float64 | np.complex64, np.float64 | np.complex64]:
        exp_value: np.float64 | np.complex64 = np.float64(0.0)
        stds: np.float64 | np.complex64 = np.float64(0.0)

        operators = json.loads(grouped_operators)
        for counts, paulis, coeffs in zip(counts_list, operators[0], operators[1]):
            paulis = PauliList(paulis)
            coeffs = np.array(coeffs)
            # self.logger.debug(counts.counts)
            exp_values, variances = _pauli_expval_with_variance(
                Counts(counts.counts), paulis
            )
            # exp_values, variances = _pauli_expval_with_variance(Counts(counts), paulis)
            exp_value += np.dot(exp_values, coeffs)
            # self.logger.debug(np.dot(exp_values, coeffs))
            stds += np.dot(variances**0.5, np.abs(coeffs))
            # self.logger.debug(np.dot(variances**0.5, np.abs(coeffs)))
        shots = sum(counts_list[0].counts.values())
        # shots = sum(counts_list[0].values())
        # self.logger.debug(np.sqrt(shots))
        stds /= np.sqrt(shots)

        return np.real_if_close([exp_value])[0], stds


def create_qiskit_operator(op_string: str, n_qubits: int) -> SparsePauliOp:
    """

    Args:
        op_params: pauli labels and indices. It is the 'operator' value in the Web API response.
        n_qubits: the qubit size of the quantum circuit

    Returns:
        SparsePauliOp:
    """

    # Parse op_params
    op_params = ast.literal_eval(op_string)

    # There is no need to validate the value of op_params because it has already been validated in the cloud.
    pauli_terms = []
    for op_param in op_params:
        # insert a space between label and index
        pauli_and_index_str: str = re.sub(
            r"([a-zA-Z])(\d)", r"\1 \2", op_param[0].strip()
        )  # e.g., 'X 0 Z 1'

        # I-label can be used with no index; it can appear as an independent term.
        if pauli_and_index_str == "I":
            # Complement an index;
            # Via SparsePauliOp.from_sparse_list(...), this is interpreted as 'I 0 I 1 I 2 ...'
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
        raise ParameterValueError(f"The specified operator is invalid. {e.args[0]}")


# count the number of CPUs in the docker container
def get_cgroup_cpu_count() -> int | None:
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r") as quota_file:
            quota = int(quota_file.read())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r") as period_file:
            period = int(period_file.read())
        cpu_count = os.cpu_count()
        if cpu_count is None:
            cpu_count = 1
        if quota == -1:
            # cpu quota is not set
            return cpu_count
        # not to exceed the number of CPUs of the host
        return min(quota // period, cpu_count)
    except FileNotFoundError:
        return os.cpu_count()


def get_allowed_threads() -> int:
    # maximum number of workers
    num_workers = 0
    max_allowed_threads = get_cgroup_cpu_count()
    if max_allowed_threads is None:
        max_allowed_threads = 1
    try:
        num_workers = int(MAX_THREADS)
        if num_workers > max_allowed_threads or num_workers < 1:
            raise Exception
    except Exception:
        num_workers = max_allowed_threads
    return num_workers


class CustomTimedRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(
        self,
        filename,
        when="midnight",
        interval=1,
        backupCount=0,
        encoding=None,
        delay=False,
        utc=False,
        atTime=None,
    ):
        self.baseFilename = filename
        super().__init__(
            filename, when, interval, backupCount, encoding, delay, utc, atTime
        )

    def _open(self):
        """
        log file is saved as "estimator.YYYY-MM-DD.log"
        """
        current_time = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
        self.baseFilename = f"{self.baseFilename}/estimator-{current_time}.log"
        return super()._open()


def init_logger():
    # logger
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] %(message)s")
    handler = CustomTimedRotatingFileHandler(
        "/estimator/logs",
        when="midnight",
        encoding="utf-8",
        interval=1,
        backupCount=30,
        utc=True,
    )
    handler.setFormatter(formatter)
    logger = logging.getLogger(__name__)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger


# boot server
def serve():
    logger = init_logger()
    num_workers = get_allowed_threads()
    # create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=num_workers))
    estimator_pb2_grpc.add_EstimationJobServiceServicer_to_server(
        Estimator(logger), server
    )
    # listen on port PORT_NUM
    server.add_insecure_port(f"0.0.0.0:{PORT_NUM}")
    # start the server
    server.start()
    # write to logger
    logger.info("server started")
    logger.info("port: %s", PORT_NUM)
    logger.info("max_num_threads: %s", num_workers)
    # keep the server running
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
