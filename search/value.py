"""Static position evaluation → estimated P(player 0 wins).

The MCTS leaf evaluator (PLAN.md Stage 4): deliberately cheap — VP race
plus pip-weighted production plus small material/army terms, squashed
through a logistic. No longest-road DFS here (too slow for a hot leaf);
the LR award itself is already inside total_vp via the stored holder.

Evaluates *perfect* (determinized) states — hidden info was sampled by the
determinizer, so reading it here is not cheating.
"""
from __future__ import annotations

import math

from engine import Building, GameState, Phase, TOPOLOGY
from engine.rules import _legal_settlement_vertices
from engine.types import COST_CITY, COST_SETTLEMENT, VP_TO_WIN

from agents.heuristic import PIP, RESOURCE_WEIGHT

# Feature weights: one VP ≈ the unit; everything else is a fraction of a VP.
_PIP_W = 0.045       # one production pip (a settlement on a 6 ≈ 0.22 VP)
_HAND_W = 0.02       # a resource card in hand
_DEV_W = 0.08        # an unplayed dev card (avg mix of knights/VP/utility)
_KNIGHT_W = 0.05     # a played knight (progress toward Largest Army)
_PROGRESS_W = 0.5    # fully affording the next build (city/settlement)
# Expansion: without these, roads are invisible to a static leaf (no VP, no
# pips) and the search systematically underexpands — measured as low-VP
# losses against the heuristic.
_EXPAND_W = 0.18     # per open settlement spot already connected (capped)
_ROAD_W = 0.03       # per road (reach / longest-road proxy)
_SCALE = 0.55        # logistic slope per point of margin


def _player_score(state: GameState, p: int) -> float:
    ps = state.players[p]
    res_pips: dict = {}
    for v, (owner, kind) in state.buildings.items():
        if owner != p:
            continue
        mult = 1 if kind is Building.SETTLEMENT else 2
        for h in TOPOLOGY.vertex_hexes[v]:
            n = state.board.numbers[h]
            if n is not None and h != state.robber_hex:
                res = state.board.terrain[h].resource
                res_pips[res] = res_pips.get(res, 0) + mult * PIP[n]
    # Concave per resource: with bank/port-only trading, production value has
    # sharply diminishing returns within one resource (13 wheat pips are not
    # 13x one pip), and every recipe needs a mix. A linear pip sum made the
    # search place into pip-rich monocultures — every systematic-loss board
    # showed a near-zero column (wood 0 / brick 0 / ore 1 / wheat 1).
    pips = sum(
        RESOURCE_WEIGHT[r] * (n ** 0.75) * 2.2 for r, n in res_pips.items()
    )
    return (
        state.total_vp(p)
        + _PIP_W * pips
        + _HAND_W * min(ps.hand_size(), 12)
        + _DEV_W * sum(ps.dev_cards.values())
        + _KNIGHT_W * ps.knights_played
        + _PROGRESS_W * _build_progress(state, p)
        + _EXPAND_W * min(len(_legal_settlement_vertices(state, p)), 3)
        + _ROAD_W * sum(1 for owner in state.roads.values() if owner == p)
    )


def _build_progress(state: GameState, p: int) -> float:
    """Fraction of the next VP-build's cost already in hand (0..1). Makes
    'trade toward a city' lines visible to the leaf evaluator instead of
    requiring the tree to expand trade->build sequences explicitly."""
    ps = state.players[p]
    has_settlement = any(
        owner == p and kind is Building.SETTLEMENT
        for owner, kind in state.buildings.values()
    )
    if ps.cities_left > 0 and has_settlement:
        cost = COST_CITY
    elif ps.settlements_left > 0:
        cost = COST_SETTLEMENT
    else:
        return 0.0
    total = sum(cost.values())
    covered = sum(min(ps.resources[r], n) for r, n in cost.items())
    return covered / total


def win_prob_p0(state: GameState) -> float:
    """P(player 0 wins) in [0, 1]. Exact at terminal states."""
    if state.phase is Phase.GAME_OVER:
        if state.winner is None:  # unreachable in real play; be safe
            return 0.5
        return 1.0 if state.winner == 0 else 0.0
    margin = _player_score(state, 0) - _player_score(state, 1)
    # A player on the brink of winning gets an extra nudge: VP race position
    # matters more than smooth features near the end.
    for p, sign in ((0, 1.0), (1, -1.0)):
        if state.total_vp(p) >= VP_TO_WIN - 1:
            margin += sign * 1.5
    p = 1.0 / (1.0 + math.exp(-_SCALE * margin))
    # Only *terminal* states may claim certainty: clamping keeps a proven win
    # (value 1.0) strictly above any static estimate, so the search always
    # prefers winning now over a merely dominant position.
    return min(0.98, max(0.02, p))
