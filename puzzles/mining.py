"""Candidate-position mining (PLAN.md Stage 6).

Positions are mined from engine self-play games — never synthesized — so
every candidate is reachable and plausible by construction. Per game we
capture:

  placement  every setup-settlement decision (the 4 draft picks; later
             picks include the opponent's visible choices, which is
             exactly the instructive part)
  robber     every robber-destination decision (7s and knights)
  endgame    wide decisions with either player at ENDGAME_VP+ (race
             calculation — where search most outclasses intuition)
  devcard    wide decisions where a dev-card play is legal (timing:
             knight / Road Building / YoP / Monopoly)
  trade      wide decisions where a bank/port trade is legal (trade-
             toward-build lines)
  midgame    remaining action-phase decisions with a real fan

A wide decision gets its most specific tag (endgame > devcard > trade >
midgame), and each tag has a per-game cap so the special types aren't
drowned by generic midgame positions. Discard decisions are excluded
(unencodable combinatorial fan; PLAN defers them). Candidates are
snapshots (`GameState.to_dict`) tagged with actor and phase; deep labeling
decides which become puzzles.
"""
from __future__ import annotations

import random

from engine import ActionType, Phase, apply_action, legal_actions, new_game
from search import MCTSEngine

MAX_ACTIONS = 6000
MIN_FAN = 4          # midgame decisions need a real choice
MINE_SIMS = 96       # mining games just need plausible play, not deep search
MINE_DETS = 3
ENDGAME_VP = 12      # either player this close to 15 = a race position
PER_GAME_CAP = {"endgame": 3, "devcard": 3, "trade": 3}

_DEV_PLAYS = {
    ActionType.PLAY_KNIGHT,
    ActionType.PLAY_ROAD_BUILDING,
    ActionType.PLAY_YEAR_OF_PLENTY,
    ActionType.PLAY_MONOPOLY,
}


def _classify(state, actions) -> str:
    if max(state.total_vp(0), state.total_vp(1)) >= ENDGAME_VP:
        return "endgame"
    types = {a.type for a in actions}
    if types & _DEV_PLAYS:
        return "devcard"
    if ActionType.TRADE_BANK in types:
        return "trade"
    return "midgame"


def mine_game(args: tuple) -> list[dict]:
    """Play one engine self-play game; return candidate snapshots.
    args = (seed, max_midgame) or (seed, max_midgame, net_path) — a net
    checkpoint makes the mining games themselves net-guided, so candidate
    positions come from stronger, more realistic play."""
    seed, max_midgame, *rest = args
    net = None
    if rest and rest[0]:
        from net.evaluator import get_evaluator

        net = get_evaluator(rest[0])
    engines = [
        MCTSEngine(
            simulations=MINE_SIMS,
            determinizations=MINE_DETS,
            seed=seed * 2 + i,
            net=net,
        )
        for i in (0, 1)
    ]
    for i, e in enumerate(engines):
        e.begin_game(i)
    state = new_game(seed)
    rng = random.Random(seed + 31337)

    placements: list[dict] = []
    robbers: list[dict] = []
    wide: dict[str, list[dict]] = {k: [] for k in (*PER_GAME_CAP, "midgame")}
    n = 0
    while state.phase is not Phase.GAME_OVER and n < MAX_ACTIONS:
        actor = state.player_to_act()
        actions = legal_actions(state)
        if len(actions) > 1:
            kind = actions[0].type
            if kind is ActionType.SETUP_PLACE_SETTLEMENT:
                placements.append(_snapshot(state, actor, "placement"))
            elif kind is ActionType.MOVE_ROBBER:
                robbers.append(_snapshot(state, actor, "robber"))
            elif kind is not ActionType.DISCARD and len(actions) >= MIN_FAN:
                tag = _classify(state, actions)
                wide[tag].append(_snapshot(state, actor, tag))
        action = engines[actor].select_action(state)
        apply_action(state, action)
        for e in engines:
            e.observe(state, action)
        n += 1

    picked: list[dict] = []
    for tag, cands in wide.items():
        rng.shuffle(cands)
        cap = PER_GAME_CAP.get(tag, max_midgame)
        picked.extend(cands[:cap])
    return placements + robbers + picked


def _snapshot(state, actor: int, phase: str) -> dict:
    return {"state": state.to_dict(), "actor": actor, "phase": phase}
