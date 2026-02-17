import json
from unittest.mock import MagicMock

import grpc
import pytest

from oqtopus_engine_core.interfaces.mitigator_interface.v1 import mitigator_pb2
from oqtopus_engine_mitigator.app import ErrorMitigator


def test_req_zne_mitigation_is_deprecated() -> None:
    mitigator = ErrorMitigator()
    context = MagicMock()

    response = mitigator.ReqZneMitigation(
        mitigator_pb2.ReqZneMitigationRequest(),
        context,
    )

    assert isinstance(response, mitigator_pb2.ReqZneMitigationResponse)
    context.set_code.assert_called_once_with(grpc.StatusCode.UNIMPLEMENTED)


def test_req_zne_pre_process_builds_execution_programs() -> None:
    mitigator = ErrorMitigator()
    mitigator._build_execution_programs = MagicMock(
        return_value=[
            mitigator_pb2.ZneExecutionProgram(
                scale_factor=1.0,
                repetition=0,
                program_index=0,
                suffix="s1-r0-p0",
                program="folded",
            )
        ]
    )
    context = MagicMock()

    response = mitigator.ReqZnePreProcess(
        mitigator_pb2.ReqZnePreProcessRequest(
            job_id="job-1",
            programs=['OPENQASM 3.0; include "stdgates.inc";'],
            zne_config_json=json.dumps({"enabled": True}),
        ),
        context,
    )

    assert len(response.execution_programs) == 1
    assert response.execution_programs[0].program == "folded"


def test_req_zne_post_process_computes_result_from_execution_results() -> None:
    mitigator = ErrorMitigator()
    mitigator._zne_postprocess = MagicMock(return_value=(0.9, 0.2, {"a": 1}))
    context = MagicMock()

    response = mitigator.ReqZnePostProcess(
        mitigator_pb2.ReqZnePostProcessRequest(
            grouped_operators_json=json.dumps([[['Z']], [[1.0]]]),
            zne_config_json=json.dumps({"enabled": True}),
            execution_results=[
                mitigator_pb2.ZneExecutionResult(
                    scale_factor=1.0,
                    repetition=0,
                    program_index=0,
                    counts={"0": 90, "1": 10},
                )
            ],
        ),
        context,
    )

    assert response.exp_value == 0.9
    assert response.stds == 0.2
    assert json.loads(response.metadata_json) == {"a": 1}


def test_parse_zne_config_invalid_scale_factors() -> None:
    mitigator = ErrorMitigator()
    with pytest.raises(ValueError, match="scale_factors must contain at least two values"):
        mitigator._parse_zne_config(
            json.dumps(
                {
                    "enabled": True,
                    "scale_factors": [1.0],
                    "factory": "richardson",
                    "folding": "global",
                    "num_to_average": 1,
                    "fail_open": True,
                }
            )
        )


def test_fold_program_with_physical_qubit_qasm() -> None:
    mitigator = ErrorMitigator()
    program = (
        'OPENQASM 3.0;\n'
        'include "stdgates.inc";\n'
        "bit[2] c;\n"
        "rz(pi/2) $0;\n"
        "sx $0;\n"
        "rz(pi/2) $0;\n"
        "cx $0, $1;\n"
        "c[0] = measure $0;\n"
        "c[1] = measure $1;\n"
    )

    folded = mitigator._fold_program(
        program=program,
        scale_factor=3.0,
        folding="global",
        basis_gates=["cx", "id", "rz", "sx", "x", "reset", "delay", "measure"],
    )

    assert "OPENQASM 3.0;" in folded
    assert "measure" in folded
    assert "\ns " not in folded
    assert " s " not in folded
