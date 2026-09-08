import pytest

from oqtopus_engine_core.framework import Job, OperatorItem, TranspileResult
from oqtopus_engine_core.slurm import (
    SlurmSimulatorOptions,
    build_execution_request,
    convert_transpiled_qasm,
    request_hash,
)

SAMPLING_QASM = """
OPENQASM 3;
include "stdgates.inc";
bit[2] c;
qubit[2] q;
h q[0];
cx q[0], q[1];
c[1] = measure q[0];
c[0] = measure q[1];
"""


def make_job(job_type: str, program: str) -> Job:
    return Job(
        job_id="job-1",
        device_id="large-simulator",
        shots=100,
        job_type=job_type,
        input="https://example.invalid/input.zip",
        program=[program],
        operator=[OperatorItem(pauli="Z 0", coeff=1.0)],
        transpile_result=TranspileResult(
            transpiled_program=program,
            stats={},
            virtual_physical_mapping={"qubit_mapping": {"0": 1, "1": 0}},
        ),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
    )


def test_convert_sampling_qasm_preserves_terminal_measurement_mapping():
    n_qubits, gates, mapping = convert_transpiled_qasm(
        SAMPLING_QASM,
        job_type="sampling",
    )

    assert n_qubits == 2
    assert [gate.name for gate in gates] == ["h", "cx"]
    assert mapping == {1: 0, 0: 1}


def test_convert_sampling_qasm_rejects_gate_after_measurement():
    program = SAMPLING_QASM.replace(
        "c[0] = measure q[1];",
        "c[0] = measure q[1];\nx q[0];",
    )

    with pytest.raises(ValueError, match="only terminal measurements"):
        convert_transpiled_qasm(program, job_type="sampling")


def test_convert_sampling_qasm_rejects_unmeasured_classical_bits():
    program = SAMPLING_QASM.replace("c[0] = measure q[1];", "")

    with pytest.raises(ValueError, match="every classical bit"):
        convert_transpiled_qasm(program, job_type="sampling")


def test_build_direct_estimation_request_maps_operator_and_is_stable():
    estimation_qasm = SAMPLING_QASM.split("c[1]", maxsplit=1)[0]
    job = make_job("estimation", estimation_qasm)

    request = build_execution_request(job, SlurmSimulatorOptions(n_per_node=4))

    assert request.shots is None
    assert request.measurement_mapping == {}
    assert request.operators[0].pauli == "Z 1"
    assert request_hash(request) == request_hash(request.model_copy(deep=True))


def test_build_sampling_request_without_transpiler_uses_input_program():
    job = make_job("sampling", SAMPLING_QASM)
    job.transpile_result = None
    job.transpiler_info = {"transpiler_lib": None}

    request = build_execution_request(job, SlurmSimulatorOptions(n_per_node=4))

    assert [gate.name for gate in request.gates] == ["h", "cx"]
    assert request.measurement_mapping == {1: 0, 0: 1}


def test_build_direct_estimation_request_without_transpiler_uses_identity_mapping():
    estimation_qasm = SAMPLING_QASM.split("c[1]", maxsplit=1)[0]
    job = make_job("estimation", estimation_qasm)
    job.transpile_result = None
    job.transpiler_info = {"transpiler_lib": None}

    request = build_execution_request(job, SlurmSimulatorOptions(n_per_node=4))

    assert request.operators[0].pauli == "Z 0"


def test_request_hash_includes_resolved_slurm_resources():
    estimation_qasm = SAMPLING_QASM.split("c[1]", maxsplit=1)[0]
    job = make_job("estimation", estimation_qasm)
    options = SlurmSimulatorOptions(n_nodes=1, n_per_node=1)
    request = build_execution_request(job, options)

    assert request_hash(request, options) != request_hash(
        request,
        options.model_copy(update={"n_nodes": 2}),
    )
