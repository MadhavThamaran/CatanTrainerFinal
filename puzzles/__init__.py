"""Puzzle mining, labeling, and scoring (PLAN.md Stage 6 / M6).

    python -m puzzles.pipeline --games 24 --out data/puzzles_v0.jsonl

produces JSONL puzzles: position + every legal move ranked with engine
Q-values and pre-computed points (regret table, chess.com-style). The
trainer UI (M7) serves these with no engine dependency:

    from puzzles import load_puzzles, score_move
    pts = score_move(puzzle, submitted_codec_id)
"""
from .labeling import label_candidate
from .schema import Puzzle, PuzzleMove, load_puzzles, puzzle_id
from .scoring import points_for_regret, score_move

__all__ = [
    "Puzzle",
    "PuzzleMove",
    "label_candidate",
    "load_puzzles",
    "points_for_regret",
    "puzzle_id",
    "score_move",
]
