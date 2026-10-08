"""The bot ladder (LADDER_SPEC): named rungs from pushover to boss,
packaging the archive of training generations into a progression — unlocks,
a play-Elo earned in rated games, and per-rung records.

v2 (2026-10-07): the first calibration showed the v1 rungs spanned only ~290
Elo, not the declared 1,200 (README, "Measured: the ladder is far flatter than
declared"), and the deepest-searching rung bought almost nothing. The rungs are
now STRENGTH DIALS on one bot, spaced by measurement: search depth for the top
of the range and a random-move rate (`epsilon`, `agents/noisy.py`) below it.

Rung ratings are DECLARED TARGETS (`provisional_elo`) until
`scripts/ladder_calibrate.py` measures them into
`data/ladder_calibration.json`; `rating_for` prefers the measured value, but
only while the rung is still the bot that was measured (a per-rung config
signature is stored beside the ratings). The scale is anchored: 1200 is the
raw-MCTS 160x4 bot (`ANCHOR`, the v1 "Journeyman").

Per-user ladder state is a plain dict (same style as `trainer/srs.py`):
    {"version": 2, "play_elo": 1200.0, "rungs": {"6": {"w": 3, "l": 1, "d": 0,
     "stars": 2, "unlocked": true, "last10": [1,0,1,...]}}}
Every reader tolerates a bare `{}` (a brand new user) — no required shape.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agents import EpsilonAgent, HeuristicAgent

PLAY_ELO_START = 1200.0
PLAY_ELO_K = 24.0
STAR3_MIN_GAMES = 10
STAR3_MIN_RATE = 0.6
# Bump when the rung table is re-specced: per-user rung records key on rung
# NUMBER, so older W/L/stars/unlocks would describe different bots.
LADDER_VERSION = 2

_CALIBRATION_PATH = Path("data/ladder_calibration.json")
_STARTS_UNLOCKED = {1, 2, 3}   # nobody enjoys grinding a pushover


@dataclass(frozen=True)
class Rung:
    number: int
    name: str
    kind: str              # "heuristic" (no search) | "mcts"
    net_path: str | None   # None = raw (no net) MCTS
    sims: int
    dets: int
    provisional_elo: float
    epsilon: float = 0.0   # chance per decision of a random plausible move


# Rungs 1-7 are ONE cheap bot (gen-7, 16 sims x 2 dets: search depth is flat
# from 8 to 64 sims — see the dial probe in LADDER_SPEC §8) with a falling
# random-move rate; rung 8 searches deeper. Epsilons are fitted to the probe
# (win rate vs the heuristic bot as p = 0.683 * (1 - eps/0.70)^1.52) for 100-Elo
# steps; `provisional_elo` is that target until the calibration measures it.
RUNGS: list[Rung] = [
    Rung(1, "Settler", "mcts", "checkpoints/gen7.pt", 16, 2, 687.0, 0.55),
    Rung(2, "Apprentice", "mcts", "checkpoints/gen7.pt", 16, 2, 787.0, 0.49),
    Rung(3, "Journeyman", "mcts", "checkpoints/gen7.pt", 16, 2, 887.0, 0.41),
    Rung(4, "Veteran", "mcts", "checkpoints/gen7.pt", 16, 2, 987.0, 0.31),
    Rung(5, "Expert", "mcts", "checkpoints/gen7.pt", 16, 2, 1087.0, 0.20),
    Rung(6, "Master", "mcts", "checkpoints/gen7.pt", 16, 2, 1187.0, 0.09),
    Rung(7, "Grandmaster", "mcts", "checkpoints/gen7.pt", 16, 2, 1287.0),
    Rung(8, "The Engine", "mcts", "checkpoints/gen7.pt", 160, 4, 1387.0),
]

# The scale's definition, NOT a rung: raw MCTS (no net) 160 sims x 4 dets is
# rated 1200 — the v1 "Journeyman". Calibration plays every rung against it.
ANCHOR_NUMBER = 0
ANCHOR_ELO = 1200.0
ANCHOR = Rung(ANCHOR_NUMBER, "Anchor", "mcts", None, 160, 4, ANCHOR_ELO)

_BY_NUMBER = {r.number: r for r in RUNGS}
_BY_NUMBER[ANCHOR_NUMBER] = ANCHOR    # make_bot()/get_rung() can build it; the table never lists it
MIN_RUNG = RUNGS[0].number
MAX_RUNG = RUNGS[-1].number
# Rungs whose moves take ~5 s: the UI shows a "thinks slowly" banner. None do
# now (the deepest rung is ~1.2 s per move).
SLOW_RUNGS: set[int] = set()


def get_rung(number: int) -> Rung:
    return _BY_NUMBER[number]


def rung_signature(rung_number: int) -> str:
    """Identity of the BOT a rung plays (not its name or target rating): a
    measured rating is only valid for the config it was measured on."""
    r = _BY_NUMBER[rung_number]
    blob = json.dumps([r.kind, r.net_path, r.sims, r.dets, r.epsilon])
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _load_calibration() -> dict[int, float]:
    """Measured ratings that still match the rung table. A file without a
    `config` block (the v1 format) or with a changed signature is stale and
    ignored — the rung falls back to its declared target."""
    if not _CALIBRATION_PATH.exists():
        return {}
    data = json.loads(_CALIBRATION_PATH.read_text())
    config = data.get("config", {})
    return {
        int(k): float(v)
        for k, v in data.get("elo", {}).items()
        if int(k) in _BY_NUMBER and config.get(k) == rung_signature(int(k))
    }


_CALIBRATED = _load_calibration()


def rating_for(rung_number: int) -> float:
    if rung_number in _CALIBRATED:
        return _CALIBRATED[rung_number]
    return _BY_NUMBER[rung_number].provisional_elo


def is_measured(rung_number: int) -> bool:
    return rung_number in _CALIBRATED


def rung_table() -> list[dict]:
    """Static per-rung info for the pre-game screen — no user state."""
    return [
        {
            "number": r.number,
            "name": r.name,
            "elo": round(rating_for(r.number), 1),
            "measured": is_measured(r.number),
            "slow": r.number in SLOW_RUNGS,
        }
        for r in RUNGS
    ]


def make_bot(rung_number: int, seed: int):
    """An Agent (begin_game/observe/select_action) for this rung."""
    r = _BY_NUMBER[rung_number]
    if r.kind == "heuristic":
        base = HeuristicAgent()
    else:
        from search import MCTSEngine

        net = None
        if r.net_path is not None:
            from net.evaluator import get_evaluator

            net = get_evaluator(r.net_path)
        base = MCTSEngine(simulations=r.sims, determinizations=r.dets, seed=seed, net=net)
    return EpsilonAgent(base, r.epsilon, seed=seed + 31) if r.epsilon > 0 else base


def migrate(state: dict) -> dict:
    """One-time reset of per-rung records when the rung table was re-specced
    (`LADDER_VERSION`); the play-Elo is kept — it is on the same anchored scale."""
    if state.get("version") != LADDER_VERSION:
        if state.get("rungs"):
            state["rungs"] = {}
        state["version"] = LADDER_VERSION
    return state


# --- per-user ladder state ---


def _blank_record() -> dict:
    return {"w": 0, "l": 0, "d": 0, "stars": 0, "unlocked": False, "last10": []}


def is_unlocked(state: dict, rung_number: int) -> bool:
    if rung_number in _STARTS_UNLOCKED:
        return True
    return bool(state.get("rungs", {}).get(str(rung_number), {}).get("unlocked"))


def _stars(record: dict) -> int:
    last10 = record["last10"]
    if (
        len(last10) >= STAR3_MIN_GAMES
        and sum(last10[-STAR3_MIN_GAMES:]) / STAR3_MIN_GAMES >= STAR3_MIN_RATE
    ):
        return 3
    if record["w"] >= 3:
        return 2
    if record["w"] >= 1:
        return 1
    return 0


def record_game(state: dict, rung_number: int, rated: bool, winner: str) -> dict:
    """Apply one finished game's result to `state` (mutated in place).

    `winner`: "you" | "bot" | "draw". Rated games move W/L/stars/Elo;
    casual games move NEITHER (LADDER_SPEC §2) but a win still unlocks
    the next rung either way — a win is a win. Returns the UI-facing
    deltas for a "rating N -> M; unlocked Master!" toast."""
    rungs = state.setdefault("rungs", {})
    record = rungs.setdefault(str(rung_number), _blank_record())
    elo_before = state.get("play_elo", PLAY_ELO_START)
    elo_after = elo_before
    stars_before = record["stars"]

    if rated:
        outcome = 1.0 if winner == "you" else 0.0 if winner == "bot" else 0.5
        if winner == "you":
            record["w"] += 1
        elif winner == "bot":
            record["l"] += 1
        else:
            record["d"] += 1
        record["last10"] = (record["last10"] + [1 if winner == "you" else 0])[
            -STAR3_MIN_GAMES:
        ]
        record["stars"] = _stars(record)
        expected = 1.0 / (1.0 + 10 ** ((rating_for(rung_number) - elo_before) / 400.0))
        elo_after = elo_before + PLAY_ELO_K * (outcome - expected)
        state["play_elo"] = elo_after

    unlocked_next = None
    if winner == "you":
        nxt = rung_number + 1
        if nxt <= MAX_RUNG:
            nxt_record = rungs.setdefault(str(nxt), _blank_record())
            if not nxt_record["unlocked"]:
                nxt_record["unlocked"] = True
                unlocked_next = nxt

    return {
        "rated": rated,
        "play_elo_before": round(elo_before, 1),
        "play_elo_after": round(elo_after, 1),
        "stars_before": stars_before,
        "stars_after": record["stars"],
        "unlocked_next": unlocked_next,
    }
