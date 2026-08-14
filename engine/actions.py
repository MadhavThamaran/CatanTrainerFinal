"""Flat action space.

Every decision point in the game — including forced sub-decisions inside a
7-sequence (discards, robber move) and Road Building's free placements — is
an ordinary Action, so search trees and the puzzle UI see one uniform
interface: `legal_actions(state)` / `apply_action(state, action)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .types import Resource


class ActionType(Enum):
    # setup phase
    SETUP_PLACE_SETTLEMENT = auto()   # vertex
    SETUP_PLACE_ROAD = auto()         # edge
    # turn skeleton
    ROLL = auto()
    END_TURN = auto()
    # forced sub-decisions
    DISCARD = auto()                  # resources = multiset to discard
    MOVE_ROBBER = auto()              # hex (steal is automatic in 1v1)
    # builds / buys
    BUILD_ROAD = auto()               # edge (free while free_roads > 0)
    BUILD_SETTLEMENT = auto()         # vertex
    BUILD_CITY = auto()               # vertex
    BUY_DEV_CARD = auto()
    # development card plays
    PLAY_KNIGHT = auto()
    PLAY_ROAD_BUILDING = auto()
    PLAY_YEAR_OF_PLENTY = auto()      # resources = the 2 gained
    PLAY_MONOPOLY = auto()            # get = declared resource
    # maritime trade (the only trade in this variant)
    TRADE_BANK = auto()               # give -> get at best owned ratio


@dataclass(frozen=True)
class Action:
    type: ActionType
    player: int
    vertex: int | None = None
    edge: int | None = None
    hex: int | None = None
    give: Resource | None = None
    get: Resource | None = None
    resources: tuple[Resource, ...] | None = None

    def __repr__(self) -> str:  # compact, log-friendly
        parts = [self.type.name, f"p{self.player}"]
        for name in ("vertex", "edge", "hex"):
            v = getattr(self, name)
            if v is not None:
                parts.append(f"{name[0]}={v}")
        if self.give is not None:
            parts.append(f"give={self.give.value}")
        if self.get is not None:
            parts.append(f"get={self.get.value}")
        if self.resources is not None:
            parts.append("+".join(r.value for r in self.resources))
        return f"<{' '.join(parts)}>"
