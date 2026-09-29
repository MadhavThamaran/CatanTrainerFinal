"""Puzzle data model (PLAN.md Stage 6).

A puzzle is a fully-labeled decision point: the position, every legal move
with its engine Q-value and pre-computed points, admission metadata, and a
light explanation. Stored as JSONL — one puzzle per line — so the trainer
UI (M7) needs no engine at serve time.

NOTE: `state` is the PERFECT game state (it includes the opponent's hand
and the dev-deck order, which re-labeling needs). It is server-side data;
a client must only be shown the observation-level view
(`GameState.from_dict(state).observation(actor)`).

Moves are keyed by their ActionCodec index — the stable, UI-independent
identifier for a move (discard decisions are never puzzles, so every legal
move is encodable).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class PuzzleMove:
    codec_id: int
    action: str          # human-readable repr
    q: float             # win prob for the actor, averaged over label seeds
    visits: int          # total visits across label runs
    points: int          # pre-computed score for submitting this move
    rank: int            # 0 = best


@dataclass
class Puzzle:
    id: str
    phase: str  # placement | robber | endgame | devcard | trade | midgame
    actor: int
    state: dict                   # GameState.to_dict() (perfect, server-side)
    moves: list[PuzzleMove]       # every legal move, ranked best-first
    best_codec_id: int
    gap: float                    # Q(best) - Q(2nd): the clarity margin
    difficulty: str               # crude proxy from gap: easy/medium/hard
    explanation: str              # one-line template (full explanations: M8)
    label_config: dict = field(default_factory=dict)
    # Placement puzzles: the labeled ROAD decision that follows the best
    # settlement (chess-style follow-up line). Keys: parent_codec_id,
    # state (post-settlement), moves [{codec_id, action, q, visits, points,
    # rank}], best_codec_id, gap. None for single-stage puzzles.
    followup: dict | None = None
    # EXPLAIN_SPEC: {"best": Facts, "second": Facts} for the top-2 ranked
    # moves — stored so the UI (and a future LLM verbalizer) can re-render
    # without recomputation. None for puzzles admitted before this shipped
    # or not yet backfilled by scripts/annotate_explanations.py.
    facts: dict | None = None

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "Puzzle":
        d = json.loads(line)
        d["moves"] = [PuzzleMove(**m) for m in d["moves"]]
        return cls(**d)


def puzzle_id(state_dict: dict, actor: int) -> str:
    payload = json.dumps(state_dict, sort_keys=True) + f"|{actor}"
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def load_puzzles(path: str) -> list[Puzzle]:
    with open(path) as f:
        return [Puzzle.from_json(line) for line in f if line.strip()]
