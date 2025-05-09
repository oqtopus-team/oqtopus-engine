import ast
import datetime
import logging
import os
from concurrent import futures
from io import TextIOWrapper
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import grpc
import numpy as np
import qiskit.qasm3
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from multiprog_interface.v1.multiprog_pb2 import (
    DESCRIPTOR,
    CombineRequest,
    CombineResponse,
    Status,
)
from multiprog_interface.v1.multiprog_pb2_grpc import (
    CircuitCombinerService,
    add_CircuitCombinerServiceServicer_to_server,
)
from qiskit import QuantumCircuit

# port number for gRPC server
PORT_NUM = os.getenv("COMBINER_PORT", "5002")
MAX_WORKERS = os.getenv("COMBINER_WORKERS", "0")


# Exception class in case of invalid number of qubits
class InvalidQubitsError(Exception):
    """Exception class in case of invalid number of qubits."""

    def __init__(self, total_qubits: int, limit_qubits: int) -> None:
        self.total_qubits = total_qubits
        self.limit_qubits = limit_qubits

    def __str__(self) -> str:
        """Return the error message.

        Returns:
            str: error message.

        """
        return f"total qubits must be less than {self.limit_qubits}. current qubits: {self.total_qubits}"   # noqa: E501


# response
class CircuitCombiner(CircuitCombinerService):
    """Combine quantum circuits.

    This class combines quantum circuits and returns the combined circuit qasm.

    """

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def Combine(  # noqa: N802
        self,
        request: CombineRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> CombineResponse:
        """Combine quantum circuits.

        Args:
            request: request with the array of qasm strings and max qubits.
            context: servicer context.

        Returns:
            CombineResponse: response with the combined circuit qasm.

        """
        try:
            # deal with request JSON-array
            self.logger.debug("start combine_circuit with request: %s", request)
            qasm_list = self.deal_with_request_qasm(request=request)
            maxqubits = request.max_qubits
            # combine circuits
            self.logger.debug("maxqubits: %s", maxqubits)
            combined_status, combined_circuit, combined_qubits_list = (
                self.combine_circuits(qasm_list, maxqubits)
            )
            # e.g.
            # For combined circuits such as [c1 (3-qubit), c2 (2-qubit), c3 (1-qubit)]
            # in total 6 qubits, combined_qubits_list is [1, 2, 3] (not [3, 2, 1]) for
            # the convenience of the measurement and division
            self.logger.debug("finish combine_circuit")
            self.logger.debug("combined_status: %s", combined_status)
            self.logger.debug("combined_circuit: %s", combined_circuit)
            self.logger.debug("combined_qubits_list: %s", combined_qubits_list)
            return CombineResponse(
                combined_status=combined_status,
                combined_qasm=combined_circuit,
                combined_qubits_list=combined_qubits_list,
            )
        except ValueError:
            self.logger.exception("Invalid Request")
            return CombineResponse(
                combined_status=Status.STATUS_FAILURE,
                combined_qasm="",
                combined_qubits_list=[],
            )
        except Exception:
            self.logger.exception("Exception")
            return CombineResponse(
                combined_status=Status.STATUS_FAILURE,
                combined_qasm="",
                combined_qubits_list=[],
            )

    def deal_with_request_qasm(self, request: CombineRequest) -> list[str]:
        """Convert the input string of qasm array to list of qasm strings.

        Args:
            request: request with the array of qasm strings.

        Returns:
            list[str]: list of qasm strings.

        Raises:
            ValueError: If invalid input QASM array.

        """
        try:
            # remove double-quote
            input_qasm_array = request.qasm_array.replace(r"\"", '"')
            # convert str to list[str]
            return ast.literal_eval(f"{input_qasm_array}")
        except Exception as e:
            self.logger.exception("Invalid input QASM array")
            msg = f"Invalid input QASM array: {request}"
            raise ValueError(msg) from e

    # Combine quantum circuits
    def combine_circuits(
        self,
        qasm_list: list[str],
        max_qubits: int = 64,
    ) -> tuple:
        """Combine quantum circuits.

        Args:
            qasm_list: list of qasm strings.
            max_qubits: maximum number of total qubits allowed.

        Returns:
            tuple: combined_status, combined_circuit, combined_qubits_list.

        """
        # array of circuits
        circuits = []
        # total number of qubits
        total_qbits = 0
        # total number of classical bits
        total_clbits = 0
        try:
            # count total num of bits, and append circuits
            for one_qasm in qasm_list:
                self.logger.debug(one_qasm)
                circuit = qiskit.qasm3.loads(one_qasm)
                circuits.append(circuit)
                total_qbits += circuit.num_qubits
                total_clbits += circuit.num_clbits
            # check if the total qubits is less than max_qubits
            max_limit_qubits = max(max_qubits, 0)
            if total_qbits > max_limit_qubits:
                self.logger.error(
                    "total qubits must be less than %s. current qubits: %s",
                    max_limit_qubits,
                    total_qbits,
                )
                return Status.STATUS_INVALID_QUBIT_SIZE, None, []
            # combine circuits
            combined_circuit = QuantumCircuit(total_qbits, total_clbits)
            combined_qubits_list: list = []
            quantum_bit_index = 0
            classical_bit_index = 0
            # combine circuits to construct combined_circuit
            for one_circuit in circuits:
                qbit_array = np.arange(one_circuit.num_qubits) + quantum_bit_index
                clbit_array = np.arange(one_circuit.num_clbits) + classical_bit_index
                combined_circuit.append(
                    one_circuit, list(qbit_array), list(clbit_array)
                )
                # increment indices
                quantum_bit_index += one_circuit.num_qubits
                classical_bit_index += one_circuit.num_clbits
                # save combined_qubits_list
                # the order of the combined_qubits_list is reversed for
                # the convenience of the measurement and division
                combined_qubits_list = [one_circuit.num_clbits, *combined_qubits_list]
            combined_circuit_obj = qiskit.qasm3.dumps(combined_circuit.decompose())
        except Exception:
            self.logger.exception("Exception")
            return Status.STATUS_FAILURE, None, []
        else:
            return (
                Status.STATUS_SUCCESS,
                combined_circuit_obj,
                # re-order the list to match the order of the input circuits
                combined_qubits_list,
            )


def get_cgroup_cpu_count() -> int:
    """Get the number of CPUs in the docker container.

    Returns:
        int: the number of CPUs in the docker container.

    """
    cpu_count = os.cpu_count() or 1
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text("utf-8"))
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text("utf-8"))
        if quota == -1:
            # cpu quota is not set
            return cpu_count
        # not to exceed the number of CPUs of the host
        return min(quota // period, cpu_count)
    except FileNotFoundError:
        return cpu_count


def get_allowed_threads() -> int:
    """Get the number of allowed threads.

    Returns:
        int: the number of allowed threads.

    """
    # maximum number of workers
    num_workers = 0
    max_allowed_threads = get_cgroup_cpu_count()
    try:
        num_workers = int(MAX_WORKERS)
        if num_workers > max_allowed_threads or num_workers < 1:
            num_workers = max_allowed_threads
    except ValueError:
        num_workers = max_allowed_threads
    return num_workers


class CustomTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Customize TimedRotatingFileHandler to set log file name in the customized form.

    The log file name is in the form of circuit_combiner-YYYY-MM-DD.log.

    """

    def __init__(  # noqa: PLR0913,PLR0917
        self,
        log_dir: str,
        when: str = "midnight",
        interval: int = 1,
        backup_count: int = 0,
        encoding: str | None = None,
        delay: bool = False,  # noqa: FBT001,FBT002
        utc: bool = False,  # noqa: FBT001,FBT002
        at_time: datetime.time | None = None,
    ) -> None:
        # log file directory
        self.log_dir = log_dir
        # set initial log file name
        self.baseFilename = self.log_path()

        super().__init__(
            self.baseFilename,
            when,
            interval,
            backup_count,
            encoding,
            delay,
            utc,
            at_time,
        )
        # set custom namer
        self.namer = self.custom_namer

    def custom_namer(self, default_name: str) -> str:  # noqa: ARG002
        """Customize log file name in the form of circuit_combiner-YYYY-MM-DD.log.

        Args:
            default_name: Not used in this override.

        Returns:
            str: log file path.

        """
        return self.log_path()

    def _open(self) -> TextIOWrapper:
        # set initial log file name again when the file is opened
        self.baseFilename = self.log_path()

        return super()._open()

    def log_path(self) -> str:
        """Get the log file path.

        Returns:
            str: log file path.

        """
        current_time = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
        return f"{self.log_dir}/circuit_combiner-{current_time}.log"


def _init_logger() -> logging.Logger:
    # logger
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] %(message)s")
    handler = CustomTimedRotatingFileHandler(
        "./logs",  # log file directory
        when="midnight",
        encoding="utf-8",
        interval=1,
        backup_count=30,
        utc=True,
    )
    handler.setFormatter(formatter)
    logger = logging.getLogger(__name__)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger


# boot server
def _serve() -> None:
    logger = _init_logger()
    num_workers = get_allowed_threads()
    # create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=num_workers))
    add_CircuitCombinerServiceServicer_to_server(CircuitCombiner(logger), server)

    service_names = (
        DESCRIPTOR.services_by_name["CircuitCombinerService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    # listen on port PORT_NUM
    server.add_insecure_port(f"0.0.0.0:{PORT_NUM}")
    # start the server
    server.start()
    # write to logger
    logger.info("server started")
    logger.info("port: %s", PORT_NUM)
    logger.info("max_workers: %s", num_workers)
    # keep the server running
    server.wait_for_termination()


if __name__ == "__main__":
    _serve()
