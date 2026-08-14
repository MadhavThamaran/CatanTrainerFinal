"""Move scoring: regret -> points (PLAN.md Stage 6).

The user's move `m` is scored by its regret in win-probability space,
`delta = Q(best) - Q(m)` — the Catan analogue of centipawn loss. Regret is
already position-normalized (win-prob units) and admission bounds sharpness
from below, so one fixed piecewise table serves every puzzle. Any move
within TIE_EPSILON of the best is a full-credit tie (epsilon sits at the
measured Q-noise floor of the labeling budget).
"""
from __future__ import annotations

TIE_EPSILON = 0.01

# (max regret inclusive, points) — PLAN.md Stage 6 table.
POINT_BANDS = [
    (0.02, 75),
    (0.05, 40),
    (0.10, 10),
    (0.15, 0),
]
BLUNDER_POINTS = -25
BEST_POINTS = 100


def points_for_regret(delta: float) -> int:
    if delta <= TIE_EPSILON:
        return BEST_POINTS
    for cap, pts in POINT_BANDS:
        if delta <= cap:
            return pts
    return BLUNDER_POINTS


def score_move(puzzle, codec_id: int) -> int:
    """Points for submitting the move with this codec id (raises KeyError
    for a move that is not legal in the puzzle position)."""
    for m in puzzle.moves:
        if m.codec_id == codec_id:
            return m.points
    raise KeyError(f"codec id {codec_id} is not a legal move of puzzle {puzzle.id}")
