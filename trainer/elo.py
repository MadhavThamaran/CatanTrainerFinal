"""Puzzle-Elo ratings, puzzle selection, and persistence (M7, PLAN Stage 7).

User and puzzles share one rating pool: the submitted move's points map to
a game score s = clamp(points/100, 0, 1); the user gains what the puzzle
loses. Puzzles start from their gap-based difficulty proxy and converge to
real difficulty as attempts accumulate — superseding the crude proxy, as
the plan intended. Only the FIRST attempt at a puzzle is rated.

Selection mixes phases (PLAN default: 40% placement when available) and
prefers unseen puzzles near the user's rating.
"""
from __future__ import annotations

import random

USER_K = 32.0
PUZZLE_K = 16.0
INITIAL_USER = 1500.0
INITIAL_BY_DIFFICULTY = {"easy": 1350.0, "medium": 1550.0, "hard": 1750.0}
PLACEMENT_SHARE = 0.4


def expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


class Ratings:
    """One user's rating pool, backed by a `Store` (accounts-layer seam —
    HOSTING.md step 1): `JsonStore` locally, `PgStore` when hosted."""

    def __init__(self, store, user_id: int):
        self._store = store
        self._user_id = user_id
        d = store.load_ratings(user_id) or {}
        self.user: float = d.get("user_rating", INITIAL_USER)
        self.puzzles: dict = d.get("puzzles", {})   # pid -> {rating, attempts, best_points}
        self.history: list = d.get("history", [])

    # --- persistence ---

    def save(self) -> None:
        self._store.save_ratings(
            self._user_id,
            {
                "user_rating": self.user,
                "puzzles": self.puzzles,
                "history": self.history[-2000:],
            },
        )

    # --- ratings ---

    def puzzle_entry(self, pid: str, difficulty: str) -> dict:
        return self.puzzles.setdefault(
            pid,
            {
                "rating": INITIAL_BY_DIFFICULTY.get(difficulty, 1550.0),
                "attempts": 0,
                "best_points": None,
            },
        )

    def record(
        self,
        pid: str,
        difficulty: str,
        points: int,
        phase: str | None = None,
        regret: float | None = None,
    ) -> dict:
        """Apply a submission. Rated only on the first attempt.

        `phase`/`regret` enrich the attempt log for the weakness dashboard
        (DASHBOARD_SPEC §0) — every attempt logged without them is a thin
        data point forever, so callers should always pass them."""
        from datetime import datetime, timezone

        entry = self.puzzle_entry(pid, difficulty)
        rated = entry["attempts"] == 0
        before = self.user
        if rated:
            s = max(0.0, min(1.0, points / 100.0))
            e = expected(self.user, entry["rating"])
            self.user += USER_K * (s - e)
            entry["rating"] -= PUZZLE_K * (s - e)
        entry["attempts"] += 1
        if entry["best_points"] is None or points > entry["best_points"]:
            entry["best_points"] = points
        self.history.append(
            {
                "pid": pid,
                "points": points,
                "rated": rated,
                "phase": phase,
                "regret": regret,
                "puzzle_rating": round(entry["rating"], 1),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        self.save()
        return {
            "rated": rated,
            "user_before": round(before, 1),
            "user_after": round(self.user, 1),
            "puzzle_rating": round(entry["rating"], 1),
        }

    # --- selection ---

    def pick(self, puzzles: list, rng: random.Random) -> "object":
        """Choose the next puzzle: phase mixture, then rating proximity,
        preferring puzzles never attempted (or never solved perfectly)."""
        def eligible(pool):
            fresh = [p for p in pool if p.id not in self.puzzles]
            if fresh:
                return fresh
            imperfect = [
                p for p in pool
                # best_points is None until first solved (viewing a puzzle
                # creates an entry), so coalesce before comparing.
                if (self.puzzles.get(p.id, {}).get("best_points") or 0) < 100
            ]
            return imperfect or pool

        placement = [p for p in puzzles if p.phase == "placement"]
        rest = [p for p in puzzles if p.phase != "placement"]
        if placement and (not rest or rng.random() < PLACEMENT_SHARE):
            pool = eligible(placement)
        else:
            pool = eligible(rest or placement)

        def proximity(p):
            r = self.puzzles.get(p.id, {}).get(
                "rating", INITIAL_BY_DIFFICULTY.get(p.difficulty, 1550.0)
            )
            return abs(r - self.user)

        pool = sorted(pool, key=proximity)[:8]
        return rng.choice(pool)
