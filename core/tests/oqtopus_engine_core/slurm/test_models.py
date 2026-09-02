import pytest
from pydantic import ValidationError

from oqtopus_engine_core.slurm import (
    SlurmSimulatorOptions,
    SlurmState,
    normalize_slurm_state,
)


def test_simulator_options_resolve_minimum_nodes_from_configured_capacity():
    options = SlurmSimulatorOptions(n_per_node=2, timeout_seconds=60)

    resolved = options.resolve(
        n_qubits=30,
        qubits_per_node=28,
        max_nodes=1024,
        max_n_per_node=48,
        max_timeout_seconds=432000,
    )

    assert resolved.n_nodes == 4


def test_simulator_options_reject_bool_and_unknown_fields():
    with pytest.raises(ValidationError):
        SlurmSimulatorOptions.model_validate({"n_nodes": True})
    with pytest.raises(ValidationError):
        SlurmSimulatorOptions.model_validate({"partition": "admin"})


def test_simulator_options_reject_too_few_nodes():
    options = SlurmSimulatorOptions(n_nodes=1)

    with pytest.raises(ValueError, match="at least 4"):
        options.resolve(
            n_qubits=32,
            qubits_per_node=30,
            max_nodes=1024,
            max_n_per_node=48,
            max_timeout_seconds=432000,
        )


def test_simulator_options_reject_non_positive_qubits_per_node():
    options = SlurmSimulatorOptions()

    with pytest.raises(ValueError, match="qubits_per_node must be positive"):
        options.resolve(
            n_qubits=30,
            qubits_per_node=0,
            max_nodes=1024,
            max_n_per_node=48,
            max_timeout_seconds=432000,
        )


@pytest.mark.parametrize("state", ["SUSPENDED", "RESIZING", "STOPPED"])
def test_suspended_allocation_states_remain_active(state):
    assert normalize_slurm_state(state) is SlurmState.RUNNING
