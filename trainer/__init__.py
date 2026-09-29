"""The tactics trainer app (M7, PLAN Stage 7).

    uv run python -m trainer.server --puzzles data/puzzles_v1.jsonl

Serves puzzles from the M6 JSONL at http://localhost:8321 — observation-
level positions on an SVG board, click-to-move, regret-table points, and
puzzle-Elo ratings shared between the user and the puzzle pool. No engine
runs at request time.
"""
from .elo import Ratings, expected
from .layout import LAYOUT
from .service import TrainerService
from .store import JsonStore, PgStore, Store, get_store

__all__ = [
    "LAYOUT",
    "JsonStore",
    "PgStore",
    "Ratings",
    "Store",
    "TrainerService",
    "expected",
    "get_store",
]
