"""Random board generation with strict-product constraints (rules.md §4).

strict_product_mode enforces:
  - exact terrain / number / port multisets
  - no 6 or 8 adjacent to any 6 or 8
  - no 2 or 12 adjacent to any 2 or 12

Numbers are assigned by rejection sampling (shuffle tokens, validate, retry):
the constraints are loose enough that acceptance takes a handful of tries.
Port *positions* are fixed slots on the coastal ring (standard spacing);
port *types* are randomized (rules.md §4.3 step 4).

Optional anti-clumping quality constraints from rules.md §4.2 are TODO and
deliberately deferred to Stage 2 polish (see PLAN.md); the seam is
`_quality_ok`.
"""
from __future__ import annotations

import random

from .board import Board
from .topology import TOPOLOGY
from .types import NUMBER_TOKENS, PORT_COUNTS, TERRAIN_COUNTS, Terrain

_HIGH = {6, 8}   # mutually non-adjacent group
_LOW = {2, 12}   # mutually non-adjacent group

_MAX_NUMBER_ATTEMPTS = 100_000


def generate_board(rng: random.Random) -> Board:
    """Generate a valid strict_product_mode board."""
    terrain = [t for t, n in TERRAIN_COUNTS.items() for _ in range(n)]
    rng.shuffle(terrain)
    desert_hex = terrain.index(Terrain.DESERT)
    non_desert = [h for h in range(TOPOLOGY.num_hexes) if h != desert_hex]

    tokens = list(NUMBER_TOKENS)
    for _ in range(_MAX_NUMBER_ATTEMPTS):
        rng.shuffle(tokens)
        numbers: list[int | None] = [None] * TOPOLOGY.num_hexes
        for h, tok in zip(non_desert, tokens):
            numbers[h] = tok
        if _numbers_ok(numbers) and _quality_ok(numbers, terrain):
            break
    else:  # pragma: no cover - astronomically unlikely
        raise RuntimeError("board generation failed to satisfy number constraints")

    port_types = [p for p, n in PORT_COUNTS.items() for _ in range(n)]
    rng.shuffle(port_types)
    ports = dict(zip(TOPOLOGY.port_slot_edges, port_types))

    board = Board(terrain=terrain, numbers=numbers, ports=ports)
    violations = validate_board(board)
    assert not violations, violations
    return board


def _numbers_ok(numbers: list[int | None]) -> bool:
    for h, n in enumerate(numbers):
        if n is None:
            continue
        for nb in TOPOLOGY.hex_neighbors[h]:
            m = numbers[nb]
            if m is None:
                continue
            if n in _HIGH and m in _HIGH:
                return False
            if n in _LOW and m in _LOW:
                return False
    return True


def _quality_ok(numbers: list[int | None], terrain: list[Terrain]) -> bool:
    # TODO(stage-2): optional anti-clumping constraints (rules.md §4.2):
    # triple-resource triangles, 5/6/8/9 coastal concentration, duplicate
    # high numbers on the same resource. Not enforced yet.
    return True


def validate_board(board: Board) -> list[str]:
    """Return a list of constraint violations (empty == valid). rules.md §4.3 step 5."""
    violations: list[str] = []

    terrain_counts = {t: board.terrain.count(t) for t in Terrain}
    if terrain_counts != TERRAIN_COUNTS:
        violations.append(f"terrain multiset wrong: {terrain_counts}")

    tokens = sorted(n for n in board.numbers if n is not None)
    if tokens != sorted(NUMBER_TOKENS):
        violations.append(f"number multiset wrong: {tokens}")
    desert_hexes = [h for h, t in enumerate(board.terrain) if t is Terrain.DESERT]
    for h in desert_hexes:
        if board.numbers[h] is not None:
            violations.append("desert has a number token")

    if not _numbers_ok(board.numbers):
        violations.append("6/8 or 2/12 adjacency constraint violated")

    port_counts = {p: list(board.ports.values()).count(p) for p in set(board.ports.values())}
    if port_counts != PORT_COUNTS:
        violations.append(f"port multiset wrong: {port_counts}")
    for e in board.ports:
        if len(TOPOLOGY.edge_hexes[e]) != 1:
            violations.append(f"port on non-coastal edge {e}")

    return violations
