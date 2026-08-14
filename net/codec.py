"""ActionCodec: the fixed, flat policy index space (PLAN.md Stage 5).

Every parameterized action maps to one stable index so a single policy head
covers the whole game. DISCARD is deliberately NOT encoded — its multiset
combinations are combinatorial; discard nodes keep the heuristic prior in
search and contribute no policy training target (value targets still flow
from every position).

Layout (total 370):
  [0,   54)  SETUP_PLACE_SETTLEMENT (vertex)
  [54, 126)  SETUP_PLACE_ROAD       (edge)
  [126,198)  BUILD_ROAD             (edge)
  [198,252)  BUILD_SETTLEMENT       (vertex)
  [252,306)  BUILD_CITY             (vertex)
  [306,325)  MOVE_ROBBER            (hex)
  [325,345)  TRADE_BANK             (give x get, get skips give)
  [345,360)  PLAY_YEAR_OF_PLENTY    (unordered pair w/ replacement)
  [360,365)  PLAY_MONOPOLY          (resource)
  365 ROLL, 366 END_TURN, 367 BUY_DEV_CARD, 368 PLAY_KNIGHT,
  369 PLAY_ROAD_BUILDING
"""
from __future__ import annotations

from itertools import combinations_with_replacement

from engine import Action, ActionType, Resource

_RESOURCES = list(Resource)
_RES_INDEX = {r: i for i, r in enumerate(_RESOURCES)}
_YOP_PAIRS = {
    pair: i for i, pair in enumerate(combinations_with_replacement(_RESOURCES, 2))
}

_OFF_SETUP_SETTLEMENT = 0
_OFF_SETUP_ROAD = 54
_OFF_BUILD_ROAD = 126
_OFF_BUILD_SETTLEMENT = 198
_OFF_BUILD_CITY = 252
_OFF_MOVE_ROBBER = 306
_OFF_TRADE = 325
_OFF_YOP = 345
_OFF_MONOPOLY = 360
_OFF_ROLL = 365
_OFF_END_TURN = 366
_OFF_BUY_DEV = 367
_OFF_KNIGHT = 368
_OFF_ROAD_BUILDING = 369

POLICY_SIZE = 370


def encode_action(action: Action) -> int | None:
    """Policy index for an action, or None if unencodable (DISCARD)."""
    t = action.type
    if t is ActionType.SETUP_PLACE_SETTLEMENT:
        return _OFF_SETUP_SETTLEMENT + action.vertex
    if t is ActionType.SETUP_PLACE_ROAD:
        return _OFF_SETUP_ROAD + action.edge
    if t is ActionType.BUILD_ROAD:
        return _OFF_BUILD_ROAD + action.edge
    if t is ActionType.BUILD_SETTLEMENT:
        return _OFF_BUILD_SETTLEMENT + action.vertex
    if t is ActionType.BUILD_CITY:
        return _OFF_BUILD_CITY + action.vertex
    if t is ActionType.MOVE_ROBBER:
        return _OFF_MOVE_ROBBER + action.hex
    if t is ActionType.TRADE_BANK:
        g = _RES_INDEX[action.give]
        gets = [r for r in _RESOURCES if r is not action.give]
        return _OFF_TRADE + g * 4 + gets.index(action.get)
    if t is ActionType.PLAY_YEAR_OF_PLENTY:
        return _OFF_YOP + _YOP_PAIRS[tuple(action.resources)]
    if t is ActionType.PLAY_MONOPOLY:
        return _OFF_MONOPOLY + _RES_INDEX[action.get]
    if t is ActionType.ROLL:
        return _OFF_ROLL
    if t is ActionType.END_TURN:
        return _OFF_END_TURN
    if t is ActionType.BUY_DEV_CARD:
        return _OFF_BUY_DEV
    if t is ActionType.PLAY_KNIGHT:
        return _OFF_KNIGHT
    if t is ActionType.PLAY_ROAD_BUILDING:
        return _OFF_ROAD_BUILDING
    return None  # DISCARD
