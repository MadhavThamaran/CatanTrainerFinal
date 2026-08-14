"""Shared test utilities: state builders that bypass normal play so unit
tests can craft exact positions."""
from __future__ import annotations

import random

from engine import (
    Building,
    GameState,
    Phase,
    Resource,
    ScriptedDice,
    TOPOLOGY,
    generate_board,
    new_game,
)


def make_board(seed: int = 42):
    return generate_board(random.Random(seed))


def make_main_state(board=None, dice=None, seed: int = 7) -> GameState:
    """A MAIN-phase state, player 0 to act in the action phase (no roll due),
    empty board. Tests stuff buildings/resources directly."""
    state = new_game(
        seed, board=board if board is not None else make_board(), dice=dice or ScriptedDice([2])
    )
    state.phase = Phase.MAIN
    state.needs_roll = False
    return state


def give(state: GameState, player: int, **resources: int) -> None:
    """give(state, 0, wood=2, ore=1)"""
    for name, n in resources.items():
        state.players[player].resources[Resource[name.upper()]] += n


def put_settlement(state: GameState, player: int, vertex: int) -> None:
    state.buildings[vertex] = (player, Building.SETTLEMENT)


def put_city(state: GameState, player: int, vertex: int) -> None:
    state.buildings[vertex] = (player, Building.CITY)


def put_road(state: GameState, player: int, edge: int) -> None:
    state.roads[edge] = player


def find_edge_path(
    length: int, forbidden_vertices: frozenset[int] = frozenset()
) -> tuple[list[int], list[int]]:
    """A vertex-simple path of `length` edges in the board graph, avoiding
    `forbidden_vertices`. Returns (vertices, edges)."""

    def dfs(v: int, path_v: list[int], path_e: list[int]):
        if len(path_e) == length:
            return path_v, path_e
        for e in TOPOLOGY.vertex_edges[v]:
            if e in path_e:
                continue
            w = TOPOLOGY.edge_other_vertex(e, v)
            if w in path_v or w in forbidden_vertices:
                continue
            found = dfs(w, path_v + [w], path_e + [e])
            if found:
                return found
        return None

    for start in range(TOPOLOGY.num_vertices):
        if start in forbidden_vertices:
            continue
        found = dfs(start, [start], [])
        if found:
            return found
    raise RuntimeError(f"no path of length {length} found")
