"""Decode ActionCodec ids into UI move descriptors (M7).

Each legal move becomes {codec_id, kind, target, label}: `kind` tells the
client what to make clickable ("vertex" / "edge" / "hex" / "button"),
`target` is the board entity id, `label` is human text (trade labels use
the actor's real port ratio from the position).

Board notation: a vertex is named by its adjacent hexes as
`<number><resource letter>` sorted by production, e.g. "9W·8S·5B"
(B brick, L wood/lumber, S sheep, W wheat, O ore), with a port tag when
the vertex sits on one. Edges are named by their flanking hexes, hexes by
their own token.
"""
from __future__ import annotations

from engine import GameState, Resource, TOPOLOGY, Terrain
from net.codec import (
    _OFF_BUILD_CITY,
    _OFF_BUILD_ROAD,
    _OFF_BUILD_SETTLEMENT,
    _OFF_BUY_DEV,
    _OFF_END_TURN,
    _OFF_KNIGHT,
    _OFF_MONOPOLY,
    _OFF_MOVE_ROBBER,
    _OFF_ROAD_BUILDING,
    _OFF_ROLL,
    _OFF_SETUP_ROAD,
    _OFF_SETUP_SETTLEMENT,
    _OFF_TRADE,
    _OFF_YOP,
    _RESOURCES,
    _YOP_PAIRS,
)

_YOP_BY_INDEX = {i: pair for pair, i in _YOP_PAIRS.items()}

_LETTER = {
    Terrain.FOREST: "L",     # lumber (W is wheat)
    Terrain.HILLS: "B",
    Terrain.PASTURE: "S",
    Terrain.FIELDS: "W",
    Terrain.MOUNTAINS: "O",
}
_PIP = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}


def hex_notation(board, h: int) -> str:
    t = board.terrain[h]
    n = board.numbers[h]
    if t is Terrain.DESERT or n is None:
        return "desert"
    return f"{n}{_LETTER[t]}"


def vertex_notation(board, v: int) -> str:
    parts = []
    for h in TOPOLOGY.vertex_hexes[v]:
        n = board.numbers[h]
        t = board.terrain[h]
        if n is not None and t in _LETTER:
            parts.append((_PIP[n], f"{n}{_LETTER[t]}"))
    parts.sort(reverse=True)  # most productive hex first
    name = "·".join(p for _, p in parts) if parts else "coast"
    port = _port_at(board, v)
    return f"{name} ({port})" if port else name


def edge_notation(board, e: int) -> str:
    hexes = [hex_notation(board, h) for h in TOPOLOGY.edge_hexes[e]]
    return "/".join(hexes)


def _port_at(board, v: int) -> str | None:
    for edge, port in board.ports.items():
        if v in TOPOLOGY.edge_vertices[edge]:
            return "3:1 port" if port.value == "3:1" else f"2:1 {port.value} port"
    return None


def describe_move(codec_id: int, state: GameState, actor: int) -> dict:
    i = codec_id
    board = state.board
    if _OFF_SETUP_SETTLEMENT <= i < _OFF_SETUP_ROAD:
        return _mk(i, "vertex", i, f"Settle {vertex_notation(board, i)}",
                   "setup_settlement")
    if _OFF_SETUP_ROAD <= i < _OFF_BUILD_ROAD:
        e = i - _OFF_SETUP_ROAD
        return _mk(i, "edge", e, f"Road at {edge_notation(board, e)}", "setup_road")
    if _OFF_BUILD_ROAD <= i < _OFF_BUILD_SETTLEMENT:
        e = i - _OFF_BUILD_ROAD
        free = "free road" if state.free_roads > 0 else "road"
        cat = "free_road" if state.free_roads > 0 else "build_road"
        return _mk(i, "edge", e, f"Build {free} at {edge_notation(board, e)}", cat)
    if _OFF_BUILD_SETTLEMENT <= i < _OFF_BUILD_CITY:
        v = i - _OFF_BUILD_SETTLEMENT
        return _mk(i, "vertex", v, f"Settle {vertex_notation(board, v)}",
                   "build_settlement")
    if _OFF_BUILD_CITY <= i < _OFF_MOVE_ROBBER:
        v = i - _OFF_BUILD_CITY
        return _mk(i, "vertex", v, f"City at {vertex_notation(board, v)}", "build_city")
    if _OFF_MOVE_ROBBER <= i < _OFF_TRADE:
        h = i - _OFF_MOVE_ROBBER
        return _mk(i, "hex", h, f"Robber to {hex_notation(board, h)}", "robber")
    if _OFF_TRADE <= i < _OFF_YOP:
        k = i - _OFF_TRADE
        give = _RESOURCES[k // 4]
        gets = [r for r in _RESOURCES if r is not give]
        get = gets[k % 4]
        ratio = state.trade_ratio(actor, give)
        return _mk(i, "button", None,
                   f"Trade {ratio} {give.value} → 1 {get.value}", "trade",
                   give=give.value, get=get.value, ratio=ratio)
    if _OFF_YOP <= i < _OFF_MONOPOLY:
        a, b = _YOP_BY_INDEX[i - _OFF_YOP]
        return _mk(i, "button", None, f"Year of Plenty: take {a.value} + {b.value}",
                   "year_of_plenty", resources=[a.value, b.value])
    if _OFF_MONOPOLY <= i < _OFF_ROLL:
        r = _RESOURCES[i - _OFF_MONOPOLY]
        return _mk(i, "button", None, f"Monopoly: declare {r.value}", "monopoly",
                   get=r.value)
    if i == _OFF_ROLL:
        return _mk(i, "button", None, "Roll dice", "roll")
    if i == _OFF_END_TURN:
        return _mk(i, "button", None, "End turn", "end_turn")
    if i == _OFF_BUY_DEV:
        return _mk(i, "button", None, "Buy development card", "buy_dev")
    if i == _OFF_KNIGHT:
        return _mk(i, "button", None, "Play Knight", "knight")
    if i == _OFF_ROAD_BUILDING:
        return _mk(i, "button", None, "Play Road Building", "road_building")
    raise ValueError(f"unknown codec id {codec_id}")


def _mk(codec_id: int, kind: str, target, label: str, category: str, **extra) -> dict:
    return {
        "codec_id": codec_id,
        "kind": kind,
        "target": target,
        "label": label,
        "category": category,
        **extra,
    }
