import argparse
import ast
import datetime
import json
import logging
import logging.config
import os
import time
from concurrent import futures
from io import TextIOWrapper
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import grpc  # type: ignore[import-untyped]
import numpy as np
import qiskit.qasm3  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from qiskit import QuantumCircuit

from oqtopus_engine_combiner.mp_auto import OptimalCircuitCombiner
from oqtopus_engine_core.interfaces.combiner_interface.v1.combiner_pb2 import (  # type: ignore[attr-defined]
    DESCRIPTOR,
    CombineRequest,
    CombineResponse,
    OptimalCombineRequest,
    OptimalCombineResponse,
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

    This class combines quantum circuits and returns the combined circuit program.

    """

    def Combine(  # noqa: N802
        self,
        request: CombineRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> CombineResponse:
        """Combine quantum circuits.

        Args:
            request: request with the array of program strings and max qubits.
            context: servicer context.

        Returns:
            CombineResponse: response with the combined circuit program.

        """
        start = time.perf_counter()

        try:
            # deal with request JSON-array
            logger.debug(
                "start combine_circuit",
                extra={
                    "request": request,
                },
            )

            program_list = self.deal_with_request_programs(programs=request.programs)
            maxqubits = request.max_qubits
            # combine circuits
            combined_status, combined_circuit, combined_qubits_list = (
                self.combine_circuits(program_list, maxqubits)
            )
            # e.g.
            # For combined circuits such as [c1 (3-qubit), c2 (2-qubit), c3 (1-qubit)]
            # in total 6 qubits, combined_qubits_list is [1, 2, 3] (not [3, 2, 1]) for
            # the convenience of the measurement and division

            response = CombineResponse(
                combined_status=combined_status,
                combined_program=combined_circuit,
                combined_qubits_list=combined_qubits_list,
            )

        except ValueError:
            logger.exception("invalid request")
            response = CombineResponse(
                combined_status=Status.STATUS_FAILURE,
                combined_program="",
                combined_qubits_list=[],
            )
        except Exception:
            logger.exception("failed to combine circuits")
            response = CombineResponse(
                combined_status=Status.STATUS_FAILURE,
                combined_program="",
                combined_qubits_list=[],
            )

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
                "finish combine_circuit",
                extra={
                    "elapsed_ms": round(elapsed_ms, 3),
                    "response": response,
                },
            )
        return response

    @staticmethod
    def deal_with_request_programs(programs: str) -> list[Any]:
        """Convert the input string of program array to list of program strings.

        Args:
            programs: request with the array of program strings.

        Returns:
            list[str]: list of program strings.

        Raises:
            ValueError: If invalid input program array.

        """
        try:
            # remove double-quote
            input_programs = programs.replace(r"\"", '"')
            # convert str to list[str]
            return ast.literal_eval(f"{input_programs}")
        except Exception as e:
            logger.exception("invalid input program array")
            msg = f"invalid input program array: {programs}"
            raise ValueError(msg) from e

    # Combine quantum circuits
    @staticmethod
    def combine_circuits(
        program_list: list[str],
        max_qubits: int = 64,
    ) -> tuple:
        """Combine quantum circuits.

        Args:
            program_list: list of program strings.
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
            for single_program in program_list:
                logger.debug(
                    "combining circuit",
                    extra={
                        "program": single_program,
                    },
                )
                circuit = qiskit.qasm3.loads(single_program)
                circuits.append(circuit)
                total_qbits += circuit.num_qubits
                total_clbits += circuit.num_clbits
            # check if the total qubits is less than max_qubits
            max_limit_qubits = max(max_qubits, 0)
            if total_qbits > max_limit_qubits:
                logger.error(
                    "failed to combine circuits; invalid qubit size",
                    extra={
                        "total_qubits": total_qbits,
                        "max_limit_qubits": max_limit_qubits,
                    },
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
            logger.exception("failed to combine circuits")
            return Status.STATUS_FAILURE, None, []
        else:
            return (
                Status.STATUS_SUCCESS,
                combined_circuit_obj,
                # re-order the list to match the order of the input circuits
                combined_qubits_list,
            )

    def OptimalCombine(  # noqa: N802
        self,
        request: OptimalCombineRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> CombineResponse:
        """Combine quantum circuits.

        Args:
            request: request with the array of program strings and max qubits.
            context: servicer context.

        Returns:
            CombineResponse: response with the combined circuit program.

        """
        start = time.perf_counter()

        try:
            # deal with request JSON-array
            logger.info(
                "start optimal_combine_circuit",
                extra={
                    "request": request,
                },
            )

            jobs = self.deal_with_request_programs(programs=request.programs)
            logger.debug(
                "received jobs",
                extra={
                    "jobs": jobs,
                },
            )
            device_info = json.loads(request.device_info)

            combined_groups = []
            combiner = OptimalCircuitCombiner()
            # assign qubits to each circuit and create groups to be combined
            assigned_ids, assigned_groups = combiner.assign_circuits(jobs=jobs,
                                                                     device_info=device_info
                                                                     )
            # create combined circuits for each group
            combined_groups = combiner.combine_circuits_for_groups(assigned_groups)

            combine_result = {
                "assigned_ids": assigned_ids,
                "combined_groups": combined_groups,
            }
            response = OptimalCombineResponse(
                combined_status=Status.STATUS_SUCCESS,
                combine_result=json.dumps(combine_result),
            )

            logger.debug(
                "combined circuits",
                extra={
                    "combine_result": combine_result,
                    "num_input_circuits": len(jobs),
                    "num_combined_circuits": len(combined_groups),
                    "num_unassigned_circuits": len(jobs) - len(assigned_ids),
                },
            )
            # message to make log easier to read
            achievement_msg = (
                f"combined {len(assigned_ids)}/{len(jobs)} circuits into "
                f"{len(combined_groups)} combined circuits."
            )

        except ValueError:
            logger.exception("invalid request")
            achievement_msg = "failed to combine circuits due to invalid request."
            response = OptimalCombineResponse(
                combined_status=Status.STATUS_FAILURE,
                combine_result="",
            )
        except Exception:
            logger.exception("failed to combine circuits")
            achievement_msg = "failed to combine circuits due to internal error."
            response = OptimalCombineResponse(
                combined_status=Status.STATUS_FAILURE,
                combine_result="",
            )

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "finish optimal_combine_circuit",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "response": response,
            },
        )
        logger.debug(achievement_msg)
        return response


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
    # create a gRPC server
    address = str(config_yaml["proto"].get("address") or "[::]:52013")
    # create the gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers))
    add_CombinerServiceServicer_to_server(CircuitCombiner(), server)

    service_names = (
        DESCRIPTOR.services_by_name["CombinerService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    server.add_insecure_port(address)
    logger.info(
        "server is running",
        extra={
            "address": address,
            "max_workers": max_workers,
        },
    )

    # start the server
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    args = _parse_args()
    serve(args.config, args.logging)
