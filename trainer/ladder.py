"""The bot ladder (LADDER_SPEC): named rungs from pushover to boss,
packaging the archive of training generations into a progression — unlocks,
a play-Elo earned in rated games, and per-rung records. The strength ladder
already exists as a side effect of the flywheel; this module just tables it.

Rung ratings are DECLARED GUESSES (`provisional_elo`) until
`scripts/ladder_calibrate.py` measures them into
`data/ladder_calibration.json`; `rating_for` prefers the measured value.

Per-user ladder state is a plain dict (same style as `trainer/srs.py`):
    {"play_elo": 1200.0, "rungs": {"6": {"w": 3, "l": 1, "d": 0,
     "stars": 2, "unlocked": true, "last10": [1,0,1,...]}}}
Every reader tolerates a bare `{}` (a brand new user) — no required shape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agents import HeuristicAgent

PLAY_ELO_START = 1200.0
PLAY_ELO_K = 24.0
STAR3_MIN_GAMES = 10
STAR3_MIN_RATE = 0.6

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


RUNGS: list[Rung] = [
    Rung(1, "Settler", "heuristic", None, 0, 0, 800.0),
    Rung(2, "Apprentice", "mcts", None, 32, 2, 1000.0),
    Rung(3, "Journeyman", "mcts", None, 160, 4, 1200.0),
    Rung(4, "Veteran", "mcts", "checkpoints/gen5.pt", 64, 3, 1400.0),
    Rung(5, "Expert", "mcts", "checkpoints/gen5.pt", 160, 4, 1550.0),
    Rung(6, "Master", "mcts", "checkpoints/gen7.pt", 160, 4, 1700.0),
    Rung(7, "Grandmaster", "mcts", "checkpoints/gen7.pt", 400, 4, 1850.0),
    Rung(8, "The Engine", "mcts", "checkpoints/gen7.pt", 800, 6, 2000.0),
]
_BY_NUMBER = {r.number: r for r in RUNGS}
MIN_RUNG = RUNGS[0].number
MAX_RUNG = RUNGS[-1].number
# ~4-6s/move at the deepest rungs -- the UI shows a "thinks slowly" banner.
SLOW_RUNGS = {7, 8}


def get_rung(number: int) -> Rung:
    return _BY_NUMBER[number]


def _load_calibration() -> dict[int, float]:
    if not _CALIBRATION_PATH.exists():
        return {}
    data = json.loads(_CALIBRATION_PATH.read_text())
    return {int(k): float(v) for k, v in data.get("elo", {}).items()}


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
        return HeuristicAgent()
    from search import MCTSEngine

    net = None
    if r.net_path is not None:
        from net.evaluator import get_evaluator

        net = get_evaluator(r.net_path)
    return MCTSEngine(simulations=r.sims, determinizations=r.dets, seed=seed, net=net)


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
