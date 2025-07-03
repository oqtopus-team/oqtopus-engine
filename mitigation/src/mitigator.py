import datetime
import logging
import os
import typing
from concurrent import futures
from logging.handlers import TimedRotatingFileHandler

import grpc
import mitigation_interface.v1.mitigation_pb2 as mitigator_pb2
import mitigation_interface.v1.mitigation_pb2_grpc as mitigator_pb2_grpc
import numpy as np
from qiskit import qasm3
from qiskit.circuit.quantumcircuitdata import CircuitInstruction
from qiskit.result import Counts, LocalReadoutMitigator, ProbDistribution

# port number for gRPC server
PORT_NUM = os.getenv("MITIGATOR_PORT", 5010)
MAX_THREADS = os.getenv("MITIGATOR_WORKERS", 0)


# response
class ErrorMitigator(mitigator_pb2_grpc.ErrorMitigatorService):
    def __init__(self, logger):
        self.logger = logger

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
            self.logger.info("start ro_error_mitigation-error mitigation process")
            self.logger.debug(
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
            self.logger.debug(
                "mitigated_counts:%s",
                mitigated_counts,
            )
            return mitigator_pb2.ReqMitigationResponse(counts=mitigated_counts)
        except Exception as e:
            self.logger.exception(f"mitigation process failed. Exception occurred:{e}")
        finally:
            self.logger.info("finish ro_error_mitigation-error mitigation process")

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
        self.logger.debug("bin counts is %s", bin_counts)
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
        self.logger.debug("finish error mitigation")
        return mitigated_counts


def get_measured_qubits(program: str) -> list[int]:
    """
    Extracts the indices of measured qubits from a QASM 3 program string.

    Parses the given QASM 3 program, identifies all measurement operations,
    and returns a list of qubit indices that are measured, ordered by their corresponding classical bit indices.

    Args:
        program (str): The QASM 3 program as a string.

    Returns:
        list[int]: A list of measured qubit indices, ordered by classical bit index.
    """
    try:
        qc = qasm3.loads(program)
    except Exception as e:
        raise ValueError(f"Invalid QASM 3 program: {e}")

    # Dictionary mapping classical bit index to qubit index
    measured_qubits_dict: dict[int, int] = {}

    for _instruction in qc.data:
        # for type checking
        instruction = typing.cast(CircuitInstruction, _instruction)

        if instruction.operation.name == "measure":
            clbits = [clbit._index for clbit in instruction.clbits]
            qubits = []
            for qubit in instruction.qubits:
                bit_info = qc.find_bit(qubit)
                if bit_info is None:
                    raise ValueError(f"Qubit {qubit} not found in circuit bits.")
                qubits.append(bit_info.index)
            for clbit, qubit in zip(clbits, qubits):
                measured_qubits_dict[clbit] = qubit

    # sort the measured qubits by classical bit index
    measured_qubits = [
        measured_qubits_dict[k] for k in sorted(measured_qubits_dict.keys())
    ]
    return measured_qubits


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
        self.baseFilename = f"{self.baseFilename}/error_mitigator-{current_time}.log"
        return super()._open()


def init_logger():
    # logger
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] %(message)s")
    handler = CustomTimedRotatingFileHandler(
        "/mitigator/logs",
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
    mitigator_pb2_grpc.add_ErrorMitigatorServiceServicer_to_server(
        ErrorMitigator(logger), server
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
