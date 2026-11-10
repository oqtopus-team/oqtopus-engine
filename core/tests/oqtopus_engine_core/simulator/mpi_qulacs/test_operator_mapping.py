import pytest

from oqtopus_engine_core.framework import OperatorItem
from oqtopus_engine_core.simulator import map_operator_items, normalize_qubit_mapping


def test_map_operator_items_uses_logical_to_physical_mapping():
    mapping = normalize_qubit_mapping({
        "qubit_mapping": {"0": 5, "1": 3, "2": 1}
    })

    mapped = map_operator_items(
        [
            OperatorItem(pauli="X0 Z 2", coeff=1.5),
            OperatorItem(pauli="Y 1", coeff=-0.25),
            OperatorItem(pauli="I", coeff=0.7),
        ],
        mapping,
        n_qubits=6,
    )

    assert [(term.pauli, term.coeff) for term in mapped] == [
        ("X 5 Z 1", 1.5),
        ("Y 3", -0.25),
        ("I", 0.7),
    ]


def test_map_operator_items_rejects_missing_mapping():
    with pytest.raises(ValueError, match="missing from transpile mapping"):
        map_operator_items(
            [OperatorItem(pauli="X 2", coeff=1.0)],
            {0: 0},
            n_qubits=3,
        )


def test_normalize_qubit_mapping_rejects_duplicate_physical_indices():
    with pytest.raises(ValueError, match="one-to-one"):
        normalize_qubit_mapping({"qubit_mapping": {"0": 1, "1": 1}})