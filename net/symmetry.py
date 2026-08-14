"""Dihedral board symmetries for training augmentation (M5 fix #2).

The radius-2 hex board has the dihedral group D6: 6 rotations x 2
reflections = 12 symmetries. Each induces permutations of hex / vertex /
edge ids, from which we derive:

  FEATURE_SRC[s]  (12, FEATURE_DIM) int  — encode(T_s(state)) == encode(state)[FEATURE_SRC[s]]
  POLICY_PERM[s]  (12, POLICY_SIZE) int  — index of T_s(action) given index of action

so every training sample can be augmented x12 by pure indexing: features
gathered through FEATURE_SRC, policy-target indices mapped through
POLICY_PERM. Resource-space actions (trades, YoP, monopoly, dev plays) and
scalars are spatial-invariant and map to themselves.

Geometry: axial rotation (q, r) -> (-r, q + r); reflection (q, r) -> (r, q).
Vertex/edge maps are derived from the exact fractional-axial transform of
the canonical vertex lattice points, so they are true graph automorphisms
(verified by tests that legal-action sets commute with the transform).
"""
from __future__ import annotations

from fractions import Fraction

import numpy as np

from engine import Board, GameState, TOPOLOGY
from engine.topology import _CORNER_OFFSETS

from .codec import POLICY_SIZE
from . import encode as E

NUM_SYMMETRIES = 12


def _axial_transforms():
    def rot(qr):
        q, r = qr
        return (-r, q + r)

    def ref(qr):
        q, r = qr
        return (r, q)

    transforms = []
    for do_ref in (False, True):
        for n in range(6):
            def f(qr, n=n, do_ref=do_ref):
                if do_ref:
                    qr = ref(qr)
                for _ in range(n):
                    qr = rot(qr)
                return qr

            transforms.append(f)
    return transforms


def _lattice_to_axial(p):
    x, y = p
    return (Fraction(3 * x - y, 6), Fraction(y, 3))


def _axial_to_lattice(qr):
    q, r = qr
    x, y = 2 * q + r, 3 * r
    assert x.denominator == 1 and y.denominator == 1
    return (int(x), int(y))


def _build_entity_perms():
    """hex/vertex/edge id permutations for the 12 symmetries.
    perm[old_id] = new_id (the id the entity maps TO)."""
    hex_index = {c: i for i, c in enumerate(TOPOLOGY.hex_coords)}
    # Reconstruct canonical vertex lattice points exactly as topology does.
    vertex_points = sorted(
        {
            (2 * q + r + dx, 3 * r + dy)
            for q, r in TOPOLOGY.hex_coords
            for dx, dy in _CORNER_OFFSETS
        }
    )
    vertex_index = {p: i for i, p in enumerate(vertex_points)}
    edge_index = {vv: i for i, vv in enumerate(TOPOLOGY.edge_vertices)}

    hex_perms, vertex_perms, edge_perms = [], [], []
    for f in _axial_transforms():
        hp = [hex_index[f(c)] for c in TOPOLOGY.hex_coords]
        vp = [
            vertex_index[_axial_to_lattice(f(_lattice_to_axial(p)))]
            for p in vertex_points
        ]
        ep = [
            edge_index[(min(vp[a], vp[b]), max(vp[a], vp[b]))]
            for a, b in TOPOLOGY.edge_vertices
        ]
        for perm, n in ((hp, 19), (vp, 54), (ep, 72)):
            assert sorted(perm) == list(range(n))  # bijection
        hex_perms.append(hp)
        vertex_perms.append(vp)
        edge_perms.append(ep)
    return hex_perms, vertex_perms, edge_perms


HEX_PERMS, VERTEX_PERMS, EDGE_PERMS = _build_entity_perms()


def _feature_src() -> np.ndarray:
    """src[s][i] = j such that encode(T_s(state))[i] == encode(state)[j]."""
    src = np.tile(np.arange(E.FEATURE_DIM, dtype=np.int64), (NUM_SYMMETRIES, 1))

    def block(offset: int, perm: list[int], stride: int, s: int) -> None:
        for old in range(len(perm)):
            new = perm[old]
            for k in range(stride):
                src[s, offset + new * stride + k] = offset + old * stride + k

    for s in range(NUM_SYMMETRIES):
        hp, vp, ep = HEX_PERMS[s], VERTEX_PERMS[s], EDGE_PERMS[s]
        block(E.O_TERRAIN, hp, 6, s)
        block(E.O_PIP, hp, 1, s)
        block(E.O_PORTS, vp, 6, s)
        block(E.O_PVALUE, vp, 1, s)
        block(E.O_ROBBER, hp, 1, s)
        block(E.O_BUILDINGS, vp, 4, s)
        block(E.O_ROADS, ep, 2, s)
        block(E.O_VFLAGS, vp, 3, s)
        # scalars: identity (already)
    return src


def _policy_perm() -> np.ndarray:
    """perm[s][old_action_index] = index of the transformed action."""
    from .codec import (
        _OFF_BUILD_CITY,
        _OFF_BUILD_ROAD,
        _OFF_BUILD_SETTLEMENT,
        _OFF_MOVE_ROBBER,
        _OFF_SETUP_ROAD,
        _OFF_SETUP_SETTLEMENT,
    )

    perm = np.tile(np.arange(POLICY_SIZE, dtype=np.int64), (NUM_SYMMETRIES, 1))
    for s in range(NUM_SYMMETRIES):
        hp, vp, ep = HEX_PERMS[s], VERTEX_PERMS[s], EDGE_PERMS[s]
        for off, p in (
            (_OFF_SETUP_SETTLEMENT, vp),
            (_OFF_BUILD_SETTLEMENT, vp),
            (_OFF_BUILD_CITY, vp),
        ):
            for old in range(54):
                perm[s, off + old] = off + p[old]
        for off in (_OFF_SETUP_ROAD, _OFF_BUILD_ROAD):
            for old in range(72):
                perm[s, off + old] = off + ep[old]
        for old in range(19):
            perm[s, _OFF_MOVE_ROBBER + old] = _OFF_MOVE_ROBBER + hp[old]
    return perm


FEATURE_SRC = _feature_src()
POLICY_PERM = _policy_perm()


def transform_state(state: GameState, s: int) -> GameState:
    """Apply symmetry s to a game state (board + spatial dynamic fields).
    Used by tests and available for inference-time symmetry averaging."""
    hp, vp, ep = HEX_PERMS[s], VERTEX_PERMS[s], EDGE_PERMS[s]
    new = state.clone()
    terrain = [None] * 19
    numbers = [None] * 19
    for h in range(19):
        terrain[hp[h]] = state.board.terrain[h]
        numbers[hp[h]] = state.board.numbers[h]
    new.board = Board(
        terrain=terrain,
        numbers=numbers,
        ports={ep[e]: t for e, t in state.board.ports.items()},
    )
    new.buildings = {vp[v]: b for v, b in state.buildings.items()}
    new.roads = {ep[e]: p for e, p in state.roads.items()}
    new.robber_hex = hp[state.robber_hex]
    if new.last_setup_settlement is not None:
        new.last_setup_settlement = vp[new.last_setup_settlement]
    return new
