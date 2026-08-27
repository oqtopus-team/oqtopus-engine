import argparse
import logging
import typing
from collections.abc import Mapping
from concurrent import futures
from typing import TYPE_CHECKING

import grpc  # type: ignore[import-untyped]
import numpy as np
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from opentelemetry import trace
from oqtopus_util.config import load_config, setup_logging
from qiskit import qasm3  # type: ignore[import-untyped]
from qiskit.result import (  # type: ignore[import-untyped]
    Counts,
    LocalReadoutMitigator,
    ProbDistribution,
)

from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2_grpc,
)
from oqtopus_engine_core.interfaces.mitigator_interface.v1.mitigator_pb2 import (  # type: ignore[attr-defined]
    DESCRIPTOR,
    DeviceTopology,
    ReqMitigationRequest,
    ReqMitigationResponse,
)
from oqtopus_engine_mitigator.observability import setup_observability

if TYPE_CHECKING:
    from qiskit.circuit.quantumcircuitdata import (  # type: ignore[import-untyped]
        CircuitInstruction,
    )

logger = logging.getLogger("oqtopus_engine_mitigator")
tracer = trace.get_tracer("oqtopus_engine_mitigator")

# LocalReadoutMitigator (used below) creates a vector of length 2^(#qubits).
# If #qubits is 32, it requires a memory of 32GB.
_MAX_MITIGATION_QUBITS = 32


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


# response
class ErrorMitigator(mitigator_pb2_grpc.MitigatorServiceServicer):
    """Mitigator service implementation for gRPC."""

    def ReqMitigation(  # noqa: N802
        self,
        request: ReqMitigationRequest,
        context: grpc.ServicerContext,  # noqa: ARG002
    ) -> ReqMitigationResponse:
        """Handle gRPC request for processing ro_error_mitigation error mitigation.

           This method returns mitigated counts calculated from ro_error_mitigation
           error of each qubits, measured counts, number of shots and
           index of measured qubitds.

        Args:
            request: The gRPC request containing the
                device_topology, counts, and program.
            context: The gRPC context for the request.

        Returns:
            The gRPC response containing the
                mitigated counts.

        """
        with tracer.start_as_current_span("mitigator.ReqMitigation") as span:
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
                return ReqMitigationResponse(counts=mitigated_counts)
            except Exception as e:
                logger.exception("mitigation process failed. Exception occurred")
                span.set_status(trace.StatusCode.ERROR, str(e))
            finally:
                logger.info("finish ro_error_mitigation-error mitigation process")

    @staticmethod
    def ro_error_mitigation(
        device_topology: DeviceTopology,
        counts: Mapping[str, int],
        program: str,
    ) -> dict[str, int]:
        """Apply readout error mitigation to measured counts.

        Args:
            device_topology: The device topology containing readout error rates
                for each qubit.
            counts: The measured bitstring counts keyed by binary string.
            program: The QASM 3 program that produced the counts.

        Returns:
            The mitigated counts keyed by binary string.

        Raises:
            ValueError: If the number of measured qubits is too large.

        """
        assignment_matrices = []
        qubits = device_topology.qubits
        shots = sum(counts.values())

        with tracer.start_as_current_span(
            "mitigator.ro_error_mitigation.extract_measured_qubits"
        ) as span:
            measured_qubits = get_measured_qubits(program)
            n_qubits = len(measured_qubits)
            if span.is_recording():
                span.set_attribute("mitigator.num_measured_qubits", n_qubits)
                span.set_attribute("mitigator.shots", shots)

        if n_qubits > _MAX_MITIGATION_QUBITS:
            # TODO: rename pseudo_inverse  # noqa: TD002, TD003, FIX002
            # to local_amat_inverse after the Web API schema is changed
            msg = (
                "input measured_qubits is too large, it requires a memory of over 32GB"
            )
            raise ValueError(msg)

        with tracer.start_as_current_span(
            "mitigator.ro_error_mitigation.build_calibration"
        ):
            for qubit_index in measured_qubits:
                mes_error = qubits[qubit_index].mes_error
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

        with tracer.start_as_current_span(
            "mitigator.ro_error_mitigation.quasi_probabilities"
        ):
            # TODO: count is unsigned int  # noqa: TD002, TD003, FIX002
            # in the Web API. So after getting the nearest_prob, the count is
            # cast to an int. This reduces the accuracy. As the data returned to
            # the user, it should be selectable not only counts (int) but also
            # quasi-distribution (float).
            # TODO: use expectation_value  # noqa: TD002, TD003, FIX002
            # estimation jobs should be calculated by
            # LocalReadoutMitigator.expectation_value. It needs to specify
            # memory_slots of Counts and num_bits of binary_probabilities(...)
            # to prevent the leading zeros in each bit string from being removed.
            quasi_dist = local_mitigator.quasi_probabilities(
                Counts(bin_counts, memory_slots=n_qubits)
            )
            nearest_prob: ProbDistribution = (
                quasi_dist.nearest_probability_distribution()  # type: ignore[no-any-return]
            )
            bin_prob = nearest_prob.binary_probabilities(num_bits=n_qubits)
            mitigated_counts = {k: int(v * shots) for k, v in bin_prob.items()}
        logger.debug("finish error mitigation")
        return mitigated_counts


def get_measured_qubits(program: str) -> list[int]:
    """Extract the indices of measured qubits from a QASM 3 program string.

    Parses the given QASM 3 program, identifies all measurement operations,
    and returns a list of measured qubit indices, ordered by their
    corresponding classical bit indices.

    Args:
        program (str): The QASM 3 program as a string.

    Returns:
        list[int]: A list of measured qubit indices, ordered by classical bit index.

    Raises:
        ValueError: If the program is not a valid QASM 3 program, or if a
            measured qubit or classical bit cannot be found in the circuit.

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
        msg = f"Invalid QASM 3 program: {e}"
        raise ValueError(msg) from e

    # Dictionary mapping classical bit index to qubit index
    measured_qubits_dict: dict[int, int] = {}

    for raw_instruction in qc.data:
        # for type checking
        instruction = typing.cast("CircuitInstruction", raw_instruction)

        if instruction.operation.name == "measure":
            clbits = []
            qubits = []
            for clbit in instruction.clbits:
                clbit_info = qc.find_bit(clbit)
                if clbit_info is None:
                    msg = f"Clbit {clbit} not found in circuit bits."
                    raise ValueError(msg)
                clbits.append(clbit_info.index)
            for qubit in instruction.qubits:
                bit_info = qc.find_bit(qubit)
                if bit_info is None:
                    msg = f"Qubit {qubit} not found in circuit bits."
                    raise ValueError(msg)
                qubits.append(bit_info.index)
            measured_qubits_dict.update(zip(clbits, qubits, strict=False))

    # sort the measured qubits by classical bit index
    return [measured_qubits_dict[k] for k in sorted(measured_qubits_dict.keys())]


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
    address = str(config_yaml["proto"].get("address") or "[::]:51011")

    # create the gRPC server
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=config_yaml["proto"]["grpc_options"],
    )
    mitigator_pb2_grpc.add_MitigatorServiceServicer_to_server(ErrorMitigator(), server)

    service_names = (
        DESCRIPTOR.services_by_name["MitigatorService"].full_name,
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
