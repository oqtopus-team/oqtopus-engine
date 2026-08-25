import argparse
import logging
import typing
from collections.abc import Mapping, Sequence
from concurrent import futures
from typing import TYPE_CHECKING

import grpc  # type: ignore[import-untyped]
import numpy as np
from grpc_reflection.v1alpha import reflection  # type: ignore[import-untyped]
from opentelemetry import trace
from oqtopus_util.config import load_config, setup_logging
from qiskit import qasm3  # type: ignore[import-untyped]
from qiskit.result import Counts, ProbDistribution  # type: ignore[import-untyped]
from qiskit_experiments.data_processing import (  # type: ignore[import-untyped]
    LocalReadoutMitigator,
)

from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2,
    mitigator_pb2_grpc,
)

from oqtopus_engine_core.interfaces.mitigator_interface.v1.mitigator_pb2 import (  # type: ignore[attr-defined]
    DESCRIPTOR,
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
MAX_MITIGATION_QUBITS = 32


class _MeasurementError(typing.Protocol):
    @property
    def p0m1(self) -> float: ...

    @property
    def p1m0(self) -> float: ...


class _Qubit(typing.Protocol):
    @property
    def mes_error(self) -> _MeasurementError: ...


class _DeviceTopology(typing.Protocol):
    @property
    def qubits(self) -> Sequence[_Qubit]: ...


class _MeasurementLayout(typing.NamedTuple):
    qubits: list[int]
    clbits: list[int]
    memory_slots: int


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
                if request.paulis:
                    expectation_values, standard_deviations = (
                        self.ro_error_mitigation_expectation_values(
                            device_topology,
                            counts,
                            program,
                            request.paulis,
                        )
                    )
                    logger.debug(
                        "mitigated expectation values:%s, standard deviations:%s",
                        expectation_values,
                        standard_deviations,
                    )
                    return mitigator_pb2.ReqMitigationResponse(
                        expectation_values=expectation_values,
                        standard_deviations=standard_deviations,
                    )

                mitigated_counts = self.ro_error_mitigation(
                    device_topology, counts, program
                )
                logger.debug(
                    "mitigated_counts:%s",
                    mitigated_counts,
                )
                return mitigator_pb2.ReqMitigationResponse(counts=mitigated_counts)
            except Exception as e:
                logger.exception("mitigation process failed. Exception occurred")
                if span.is_recording():
                    span.set_status(trace.StatusCode.ERROR, str(e))
            finally:
                logger.info("finish ro_error_mitigation-error mitigation process")

    @staticmethod
    def ro_error_mitigation(
        device_topology: _DeviceTopology,
        counts: Mapping[str, int],
        program: str,
    ) -> dict[str, int]:
        """Calculate mitigated sampling counts.

        Args:
            device_topology: Device readout assignment error data.
            counts: Observed counts keyed by bit string.
            program: OpenQASM 3 program containing measurements.

        Returns:
            Mitigated counts projected onto the nearest probability distribution.

        """
        shots = sum(counts.values())
        local_mitigator, layout = create_local_readout_mitigator(
            device_topology,
            program,
        )
        n_qubits = len(layout.qubits)
        qiskit_counts = Counts(dict(counts), memory_slots=layout.memory_slots)
        logger.debug("Qiskit counts is %s", qiskit_counts)
        # The Web API count is an unsigned integer, so projecting the quasi
        # distribution and casting it to counts reduces sampling-job accuracy.
        with tracer.start_as_current_span(
            "mitigator.ro_error_mitigation.quasi_probabilities"
        ) as span:
            if span.is_recording():
                span.set_attribute("mitigator.shots", shots)
            quasi_dist = local_mitigator.quasi_probabilities(
                qiskit_counts,
                qubits=list(range(n_qubits)),
                clbits=layout.clbits,
            )
            nearest_prob: ProbDistribution = (
                quasi_dist.nearest_probability_distribution()
            )
            bin_prob = nearest_prob.binary_probabilities(num_bits=n_qubits)
            mitigated_counts = {k: int(v * shots) for k, v in bin_prob.items()}
        logger.debug("finish error mitigation")
        return mitigated_counts

    @staticmethod
    def ro_error_mitigation_expectation_values(
        device_topology: _DeviceTopology,
        counts: Mapping[str, int],
        program: str,
        paulis: Sequence[str],
    ) -> tuple[list[float], list[float]]:
        """Calculate mitigated Pauli expectation values and uncertainty bounds.

        Args:
            device_topology: Device readout assignment error data.
            counts: Observed counts keyed by bit string.
            program: OpenQASM 3 program containing measurements.
            paulis: Pauli labels corresponding to the measured basis.

        Returns:
            Mitigated expectation values and standard-deviation upper bounds.

        Raises:
            ValueError: If the program, measured-qubit count, or Pauli is invalid.

        """
        n_qubits = len(paulis[0])
        for pauli in paulis:
            if len(pauli) != n_qubits or any(char not in "IXYZ" for char in pauli):
                message = f"Pauli {pauli!r} must contain {n_qubits} I/X/Y/Z characters"
                raise ValueError(message)

        local_mitigator, layout = create_local_readout_mitigator(
            device_topology,
            program,
            measurement_count=n_qubits,
        )
        qiskit_counts = Counts(dict(counts), memory_slots=layout.memory_slots)
        expectation_values = []
        standard_deviations = []

        for pauli in paulis:
            support = [
                index for index, char in enumerate(reversed(pauli)) if char != "I"
            ]
            if not support:
                expectation_values.append(1.0)
                standard_deviations.append(0.0)
                continue

            expectation_value, standard_deviation = local_mitigator.expectation_value(
                qiskit_counts,
                qubits=support,
                clbits=[layout.clbits[index] for index in support],
            )
            expectation_values.append(float(expectation_value))
            standard_deviations.append(float(standard_deviation))

        return expectation_values, standard_deviations


def create_local_readout_mitigator(
    device_topology: _DeviceTopology,
    program: str,
    *,
    measurement_count: int | None = None,
) -> tuple[LocalReadoutMitigator, _MeasurementLayout]:
    """Build a local mitigator in classical-bit measurement order.

    Args:
        device_topology: Device readout assignment error data.
        program: OpenQASM 3 program containing measurements.
        measurement_count: Number of trailing measurement destinations to use.

    Returns:
        The configured local mitigator and selected measurement layout.

    Raises:
        ValueError: If the program or measured-qubit count is invalid.

    """
    assignment_matrices = []
    with tracer.start_as_current_span(
        "mitigator.ro_error_mitigation.extract_measured_qubits"
    ) as span:
        layout = _get_measurement_layout(program, measurement_count=measurement_count)
        n_qubits = len(layout.qubits)
        if span.is_recording():
            span.set_attribute("mitigator.num_measured_qubits", n_qubits)

    # LocalReadoutMitigator creates vectors with length exponential in qubit count.
    if n_qubits > MAX_MITIGATION_QUBITS:
        message = (
            "input measured_qubits is too large, it requires a memory of over 32GB"
        )
        raise ValueError(message)

    with tracer.start_as_current_span(
        "mitigator.ro_error_mitigation.build_calibration"
    ):
        for qubit_id in layout.qubits:
            mes_error = device_topology.qubits[qubit_id].mes_error
            assignment_matrices.append(
                np.array(
                    [
                        [1 - mes_error.p0m1, mes_error.p1m0],
                        [mes_error.p0m1, 1 - mes_error.p1m0],
                    ],
                    dtype=float,
                )
            )

    return LocalReadoutMitigator(assignment_matrices), layout


def get_measured_qubits(program: str) -> list[int]:
    """Extract the indices of measured qubits from a QASM 3 program string.

    Parses the given QASM 3 program, identifies all measurement operations,
    and returns measured qubit indices ordered by their global classical bit
    indices.

    Args:
        program (str): The QASM 3 program as a string.

    Returns:
        list[int]: A list of measured qubit indices, ordered by classical bit index.

    Raises:
        ValueError: If the program is not a valid QASM 3 program, or if a
            measured qubit or classical bit cannot be found in the circuit.

    """
    return _get_measurement_layout(program).qubits


def _select_clbits(
    measurements: Sequence[tuple[int, int]],
    measurement_count: int | None,
) -> list[int]:
    measured_qubits_by_clbit = dict(measurements)
    if measurement_count is None:
        return sorted(measured_qubits_by_clbit)

    selected_clbits = []
    for clbit, _ in reversed(measurements):
        if clbit not in selected_clbits:
            selected_clbits.append(clbit)
        if len(selected_clbits) == measurement_count:
            break
    if len(selected_clbits) != measurement_count:
        message = (
            f"input measured_qubits size {len(selected_clbits)} does not match "
            f"Pauli size {measurement_count}"
        )
        raise ValueError(message)
    return sorted(selected_clbits)


def _get_measurement_layout(
    program: str,
    *,
    measurement_count: int | None = None,
) -> _MeasurementLayout:
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

    measurements: list[tuple[int, int]] = []

    for instruction_data in qc.data:
        # for type checking
        instruction = typing.cast("CircuitInstruction", instruction_data)

        if instruction.operation.name == "measure":
            for qubit, clbit in zip(
                instruction.qubits,
                instruction.clbits,
                strict=True,
            ):
                qubit_info = qc.find_bit(qubit)
                clbit_info = qc.find_bit(clbit)
                if qubit_info is None:
                    message = f"Qubit {qubit} not found in circuit bits."
                    raise ValueError(message)
                if clbit_info is None:
                    message = f"Clbit {clbit} not found in circuit bits."
                    raise ValueError(message)
                measurements.append((clbit_info.index, qubit_info.index))

    measured_qubits_by_clbit = dict(measurements)
    selected_clbits = _select_clbits(measurements, measurement_count)

    return _MeasurementLayout(
        qubits=[measured_qubits_by_clbit[clbit] for clbit in selected_clbits],
        clbits=selected_clbits,
        memory_slots=qc.num_clbits,
    )


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
