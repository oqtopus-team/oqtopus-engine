import argparse
import logging
import logging.config
import os
import typing
from concurrent import futures
from pathlib import Path

import grpc
import numpy as np
import yaml
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from qiskit import qasm3
from qiskit.circuit.quantumcircuitdata import CircuitInstruction
from qiskit.result import Counts, LocalReadoutMitigator, ProbDistribution

from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2,
    mitigator_pb2_grpc,
)

logger = logging.getLogger("oqtopus_engine_mitigator")


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


# response
class ErrorMitigator(mitigator_pb2_grpc.MitigatorService):
    def ReqMitigation(self, request, context):
        """Handle gRPC request for processing ro_error_mitigation error mitigation.

           This method returns mitigated counts calculated from ro_error_mitigation
           error of each qubits, measured counts, number of shots and
           index of measured qubitds.

        Args:
            request (mitigator_pb2_grpc.request): The gRPC request containing the
                device_topology, counts, and program.
            context (grpc.ServicerContext): The gRPC context for the request.

        Returns:
            mitigator_pb2_grpc.ReqMitigationResponse: The gRPC response containing the
                mitigated counts.

        """
        try:
            logger.info("start ro_error_mitigation-error mitigation process")
            logger.debug(
                "device_topology:%s, counts:%s, program:%s",
                request.device_topology,
                request.counts,
                request.program,
            )
            device_topology = request.device_topology
            counts = request.counts
            program = request.program
            mitigated_counts = self.ro_error_mitigation(
                device_topology, counts, program
            )
            logger.debug(
                "mitigated_counts:%s",
                mitigated_counts,
            )
            return mitigator_pb2.ReqMitigationResponse(counts=mitigated_counts)
        except Exception as e:
            logger.exception(f"mitigation process failed. Exception occurred:{e}")
        finally:
            logger.info("finish ro_error_mitigation-error mitigation process")

    def ro_error_mitigation(
        self,
        device_topology,
        counts,
        program,
    ) -> dict[str, int]:
        assignment_matrices = []
        qubits = device_topology.qubits
        shots = sum(counts.values())
        measured_qubits = get_measured_qubits(program)
        n_qubits = len(measured_qubits)

        # LocalReadoutMitigator (used below) creates a vector of length 2^(#qubits).
        if n_qubits > 32:  # If #qubits is 32, it requires a memory of 32GB.
            # TODO rename pseudo_inverse to local_amat_inverse after the Web API schema is changed
            raise ValueError(
                "input measured_qubits is too large, it requires a memory of over 32GB"
            )

        for id in measured_qubits:
            mes_error = qubits[id].mes_error
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
        logger.debug("bin counts is %s", bin_counts)
        # TODO The Web API data type for count is unsigned int.
        # So after getting the nearest_prob, the count count is cast to an int. This reduces the accuracy.
        # As the data returned to the user, it should be selectable not only counts (int) but also quasi-distribution (float).
        # TODO estimation jobs should be calculated by LocalReadoutMitigator.expectation_value
        # It needs to specify memory_slots of Counts and num_bits of binary_probabilities(...) to prevent
        # the leading zeros in each bit string from being removed.
        quasi_dist = local_mitigator.quasi_probabilities(
            Counts(bin_counts, memory_slots=n_qubits)
        )
        nearest_prob: ProbDistribution = quasi_dist.nearest_probability_distribution()  # type: ignore
        bin_prob = nearest_prob.binary_probabilities(num_bits=n_qubits)
        mitigated_counts = {k: int(v * shots) for k, v in bin_prob.items()}
        logger.debug("finish error mitigation")
        return mitigated_counts


def get_measured_qubits(program: str) -> list[int]:
    """Extracts the indices of measured qubits from a QASM 3 program string.

    Parses the given QASM 3 program, identifies all measurement operations,
    and returns a list of qubit indices that are measured, ordered by their corresponding classical bit indices.

    Args:
        program (str): The QASM 3 program as a string.

    Returns:
        list[int]: A list of measured qubit indices, ordered by classical bit index.

    """
    try:
        qc = qasm3.loads(program)
        gate_counts = qc.count_ops()
        logger.debug(
            "QASM program successfully loaded. "
            "Stats: qubits=%d, clbits=%d, depth=%d, total_gates=%d, gate_counts=%s",
            qc.num_qubits,
            qc.num_clbits,
            qc.depth(),
            sum(gate_counts.values()),
            gate_counts,
        )
    except Exception as e:
        raise ValueError(f"Invalid QASM 3 program: {e}")

    # Dictionary mapping classical bit index to qubit index
    measured_qubits_dict: dict[int, int] = {}

    for _instruction in qc.data:
        # for type checking
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

    # sort the measured qubits by classical bit index
    measured_qubits = [
        measured_qubits_dict[k] for k in sorted(measured_qubits_dict.keys())
    ]
    return measured_qubits


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
    address = str(config_yaml["proto"].get("address") or "[::]:52011")

    # create the gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    mitigator_pb2_grpc.add_MitigatorServiceServicer_to_server(ErrorMitigator(), server)
    service_names = (
        mitigator_pb2.DESCRIPTOR.services_by_name["MitigatorService"].full_name,
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
