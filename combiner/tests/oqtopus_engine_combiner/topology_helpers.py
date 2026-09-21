"""Shared QASM fixtures and topology builders used across combiner tests."""

from typing import Any

SIMPLE_1Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[1] q;\n"
    "bit[1] cbit;\n"
    "h q[0];\n"
    "measure q[0] -> cbit[0];"
)

SIMPLE_2Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[2] q;\n"
    "bit[2] cbit;\n"
    "h q[0];\n"
    "x q[0];\n"
    "cx q[0], q[1];\n"
    "measure q[0] -> cbit[0];\n"
    "measure q[1] -> cbit[1];"
)

SIMPLE_3Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[3] q;\n"
    "bit[3] cbit;\n"
    "h q[0];\n"
    "cx q[0], q[1];\n"
    "cx q[2], q[1];\n"
    "measure q[0] -> cbit[0];\n"
    "measure q[1] -> cbit[1];\n"
    "measure q[2] -> cbit[2];\n"
)

UNASSIGNABLE_3Q_QASM = (
    "OPENQASM 3;\n"
    'include "stdgates.inc";\n'
    "qubit[3] q;\n"
    "bit[3] cbit;\n"
    "h q[0];\n"
    "cx q[0], q[1];\n"
    "cx q[1], q[2];\n"
    "measure q[0] -> cbit[0];\n"
    "measure q[1] -> cbit[1];\n"
    "measure q[2] -> cbit[2];\n"
)


def make_linear_topology(n_qubits: int) -> dict[str, Any]:
    """Create a linear topology with n_qubits connected in a chain.

    Each qubit is exclusively a control (C) or target (T) node.
    Even-index nodes are controls, odd-index nodes are targets.

    Example for n_qubits=5::

        C    T    C    T    C
        0 →  1 ←  2 →  3 ←  4

    """
    qubits = [{"id": i, "position": {"x": i, "y": 0}} for i in range(n_qubits)]
    couplings = []
    for i in range(n_qubits - 1):
        if i % 2 == 0:
            couplings.append({"control": i, "target": i + 1})
        else:
            couplings.append({"control": i + 1, "target": i})
    return {"qubits": qubits, "couplings": couplings}


def make_grid_topology(rows: int, cols: int) -> dict[str, Any]:
    """Create a grid topology with rows x cols qubits connected to right/bottom neighbors.

    Each qubit is exclusively a control (C) or target (T) node, assigned
    by checkerboard parity: (row + col) even → control, odd → target.

    Example for 3x4 (rows=3, cols=4)::

         0 →  1 ←  2 →  3
         ↓    ↑    ↓    ↑
         4 ←  5 →  6 ←  7
         ↑    ↓    ↑    ↓
         8 →  9 ← 10 → 11

    """
    qubits = []
    for r in range(rows):
        for c in range(cols):
            node_id = r * cols + c
            qubits.append({"id": node_id, "position": {"x": c, "y": r}})

    couplings = []
    for r in range(rows):
        for c in range(cols):
            node_id = r * cols + c
            # Horizontal edge to right neighbor
            if c + 1 < cols:
                right = node_id + 1
                if (r + c) % 2 == 0:
                    couplings.append({"control": node_id, "target": right})
                else:
                    couplings.append({"control": right, "target": node_id})
            # Vertical edge to bottom neighbor
            if r + 1 < rows:
                below = node_id + cols
                if (r + c) % 2 == 0:
                    couplings.append({"control": node_id, "target": below})
                else:
                    couplings.append({"control": below, "target": node_id})

    return {"qubits": qubits, "couplings": couplings}


def make_grid_topology_with_defects() -> dict[str, Any]:
    """Create an 8x8 grid topology with 2 missing edges to simulate defective connections.

    Uses :func:`make_grid_topology` then removes two edges:
      - (27, 19): edge between node 27 (control) and node 19 (target)
      - (36, 37): edge between node 36 (control) and node 37 (target)

    Layout::

         0 →  1 ←  2 →  3 ←  4 →  5 ←  6 →  7
         ↓    ↑    ↓    ↑    ↓    ↑    ↓    ↑
         8 ←  9 → 10 ← 11 → 12 ← 13 → 14 ← 15
         ↑    ↓    ↑    ↓    ↑    ↓    ↑    ↓
        16 → 17 ← 18 → 19 ← 20 → 21 ← 22 → 23
         ↓    ↑    ↓         ↓    ↑    ↓    ↑
        24 ← 25 → 26 ← 27 → 28 ← 29 → 30 ← 31
         ↑    ↓    ↑    ↓    ↑    ↓    ↑    ↓
        32 → 33 ← 34 → 35 ← 36   37 ← 38 → 39
         ↓    ↑    ↓    ↑    ↓    ↑    ↓    ↑
        40 ← 41 → 42 ← 43 → 44 ← 45 → 46 ← 47
         ↑    ↓    ↑    ↓    ↑    ↓    ↑    ↓
        48 → 49 ← 50 → 51 ← 52 → 53 ← 54 → 55
         ↓    ↑    ↓    ↑    ↓    ↑    ↓    ↑
        56 ← 57 → 58 ← 59 → 60 ← 61 → 62 ← 63

    """
    topo = make_grid_topology(8, 8)
    defect_edges = {(27, 19), (36, 37)}
    topo["couplings"] = [
        c for c in topo["couplings"]
        if (c["control"], c["target"]) not in defect_edges
    ]
    return topo
