import re
from collections.abc import Mapping, Sequence
from typing import Any

# ruff: noqa: DOC201, DOC501
from oqtopus_engine_core.framework import OperatorItem

from .models import OperatorTerm

_PAULI_FACTOR = re.compile(r"([IXYZ])(\d+)")


def normalize_qubit_mapping(
    virtual_physical_mapping: Mapping[str, Any],
) -> dict[int, int]:
    """Validate and normalize Tranqu's logical-to-physical qubit mapping."""
    raw_mapping = virtual_physical_mapping.get("qubit_mapping")
    if not isinstance(raw_mapping, Mapping):
        message = "virtual_physical_mapping.qubit_mapping is required"
        raise TypeError(message)

    mapping: dict[int, int] = {}
    physical_indices: set[int] = set()
    for raw_logical, raw_physical in raw_mapping.items():
        try:
            logical = int(raw_logical)
        except (TypeError, ValueError) as exc:
            message = f"invalid logical qubit index: {raw_logical!r}"
            raise ValueError(message) from exc
        if (
            logical < 0
            or isinstance(raw_physical, bool)
            or not isinstance(raw_physical, int)
            or raw_physical < 0
        ):
            message = f"invalid qubit mapping: {raw_logical!r} -> {raw_physical!r}"
            raise ValueError(message)
        if logical in mapping or raw_physical in physical_indices:
            message = "qubit mapping must be one-to-one"
            raise ValueError(message)
        mapping[logical] = raw_physical
        physical_indices.add(raw_physical)
    return mapping


def map_operator_items(
    operators: Sequence[OperatorItem],
    virtual_to_physical: Mapping[int, int],
    *,
    n_qubits: int,
) -> list[OperatorTerm]:
    """Map Pauli factor indices while preserving labels, order, and coefficients."""
    mapped_terms: list[OperatorTerm] = []
    for operator in operators:
        compact = operator.pauli.replace(" ", "")
        if compact == "I":
            mapped_terms.append(OperatorTerm(pauli="I", coeff=operator.coeff))
            continue

        factors = list(_PAULI_FACTOR.finditer(compact))
        if not factors or "".join(match.group(0) for match in factors) != compact:
            message = f"invalid Pauli term: {operator.pauli!r}"
            raise ValueError(message)

        seen_logical: set[int] = set()
        mapped_factors: list[str] = []
        for factor in factors:
            logical = int(factor.group(2))
            if logical in seen_logical:
                message = f"duplicate qubit index in Pauli term: {logical}"
                raise ValueError(message)
            if logical not in virtual_to_physical:
                message = f"operator qubit is missing from transpile mapping: {logical}"
                raise ValueError(message)
            physical = virtual_to_physical[logical]
            if physical >= n_qubits:
                message = f"mapped operator qubit is out of range: {physical}"
                raise ValueError(message)
            mapped_factors.extend((factor.group(1), str(physical)))
            seen_logical.add(logical)
        mapped_terms.append(
            OperatorTerm(pauli=" ".join(mapped_factors), coeff=operator.coeff)
        )
    return mapped_terms
