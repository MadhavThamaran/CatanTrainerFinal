"""Longest-road computation (rules.md §11).

Longest simple path (no edge reused) in the player's road subgraph.
An opponent settlement/city breaks continuity: a path may *end* at such a
vertex but may not pass through it. The player's own buildings never break
continuity. Exhaustive DFS is fine at <= 15 road pieces per player.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .topology import TOPOLOGY

if TYPE_CHECKING:  # pragma: no cover
    from .state import GameState


def longest_road_length(state: "GameState", player: int) -> int:
    player_edges = {e for e, owner in state.roads.items() if owner == player}
    if not player_edges:
        return 0

    def blocked(v: int) -> bool:
        b = state.buildings.get(v)
        return b is not None and b[0] != player

    best = 0

    def dfs(v: int, used: set[int], depth: int) -> None:
        nonlocal best
        best = max(best, depth)
        # May start from a blocked vertex (the road ends there) but never
        # continue through one mid-path.
        if depth > 0 and blocked(v):
            return
        for e in TOPOLOGY.vertex_edges[v]:
            if e in player_edges and e not in used:
                used.add(e)
                dfs(TOPOLOGY.edge_other_vertex(e, v), used, depth + 1)
                used.discard(e)

    starts = {v for e in player_edges for v in TOPOLOGY.edge_vertices[e]}
    for v in starts:
        dfs(v, set(), 0)
    return best
