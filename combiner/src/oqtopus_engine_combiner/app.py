import argparse
import ast
import datetime
import logging
import logging.config
import os
from concurrent import futures
from io import TextIOWrapper
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import grpc
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
import numpy as np
import qiskit.qasm3
from qiskit import QuantumCircuit
import yaml

from oqtopus_engine_core.interfaces.combiner_interface.v1.combiner_pb2 import (
    DESCRIPTOR,
    CombineRequest,
    CombineResponse,
    Status,
)
from oqtopus_engine_core.interfaces.combiner_interface.v1.combiner_pb2_grpc import (
    CombinerService,
    add_CombinerServiceServicer_to_server,
)


logger = logging.getLogger("oqtopus_engine_combiner")


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
class CircuitCombiner(CombinerService):
    """Combine quantum circuits.

    This class combines quantum circuits and returns the combined circuit qasm.

    """

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
            logger.debug("start combine_circuit with request: %s", request)
            qasm_list = self.deal_with_request_qasm(request=request)
            maxqubits = request.max_qubits
            # combine circuits
            logger.debug("maxqubits: %s", maxqubits)
            combined_status, combined_circuit, combined_qubits_list = (
                self.combine_circuits(qasm_list, maxqubits)
            )
            # e.g.
            # For combined circuits such as [c1 (3-qubit), c2 (2-qubit), c3 (1-qubit)]
            # in total 6 qubits, combined_qubits_list is [1, 2, 3] (not [3, 2, 1]) for
            # the convenience of the measurement and division
            logger.debug("finish combine_circuit")
            logger.debug("combined_status: %s", combined_status)
            logger.debug("combined_circuit: %s", combined_circuit)
            logger.debug("combined_qubits_list: %s", combined_qubits_list)
            return CombineResponse(
                combined_status=combined_status,
                combined_qasm=combined_circuit,
                combined_qubits_list=combined_qubits_list,
            )
        except ValueError:
            logger.exception("Invalid Request")
            return CombineResponse(
                combined_status=Status.STATUS_FAILURE,
                combined_qasm="",
                combined_qubits_list=[],
            )
        except Exception:
            logger.exception("Exception")
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
                logger.debug(one_qasm)
                circuit = qiskit.qasm3.loads(one_qasm)
                circuits.append(circuit)
                total_qbits += circuit.num_qubits
                total_clbits += circuit.num_clbits
            # check if the total qubits is less than max_qubits
            max_limit_qubits = max(max_qubits, 0)
            if total_qbits > max_limit_qubits:
                logger.error(
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
            logger.exception("Exception")
            return Status.STATUS_FAILURE, None, []
        else:
            return (
                Status.STATUS_SUCCESS,
                combined_circuit_obj,
                # re-order the list to match the order of the input circuits
                combined_qubits_list,
            )


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



# boot server
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
    address = str(config_yaml["proto"].get("address") or "[::]:52013")    # create a gRPC server

    # create the gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers))
    add_CombinerServiceServicer_to_server(CircuitCombiner(), server)

    service_names = (
        DESCRIPTOR.services_by_name["CombinerService"].full_name,
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
