"""The weakness dashboard ("your coach's notebook") — DASHBOARD_SPEC.

Pure aggregation over data other features already produce — no engine
time, no new content. Every statement derives only from stored numbers
with explicit thresholds and minimum-n gates (same honesty contract as
EXPLAIN_SPEC/coach/lab): never invent a weakness.

Game records in `data/games/` carry the player's `user_id`, so the Form and
Cost cards read only the requesting user's games (records written before
that field existed stay visible to everyone — they came from the one local
player); the puzzle-Elo sections (global rating, skills, skill-gap leaks)
are per-user via the `Ratings` passed in.
"""
from __future__ import annotations

import json
from pathlib import Path

GAMES_DIR = Path("data/games")
PHASES = ["placement", "robber", "trade", "devcard", "endgame", "midgame"]

SKILL_GAP_THRESHOLD = 100.0
SKILL_GAP_MIN_N = 15
TAXONOMY_MIN_BLUNDERS = 5
TAXONOMY_SHARE = 0.6
MAX_LEAKS = 4

# (stage, min turn, max turn) — a coarse, documented heuristic; there is
# no canonical stage boundary in the rules, turn count is what review rows
# carry.
STAGE_BOUNDS = [
    ("setup", 0, 0),
    ("early", 1, 8),
    ("mid", 9, 20),
    ("endgame", 21, 10**9),
]

# Action-type groups for the review-taxonomy leak, matched against the
# exact label prefixes trainer/actions.py::describe_move and
# trainer/play.py::_label produce (our own controlled vocabulary, not a
# guess at free text).
_ACTION_GROUPS = [
    ("robber moves", ("Robber to",)),
    ("trades", ("Trade ",)),
    ("dev plays", (
        "Play Knight", "Play Road Building", "Year of Plenty:",
        "Monopoly:", "Buy development card",
    )),
    ("builds", ("Settle ", "Build ", "City at ")),
    ("discards", ("Discard ",)),
    ("end-turn timing", ("End turn",)),
]


def _group_for_label(label: str) -> str | None:
    for group, prefixes in _ACTION_GROUPS:
        if label.startswith(prefixes):
            return group
    return None


def _stage_for_turn(turn: int) -> str:
    for name, lo, hi in STAGE_BOUNDS:
        if lo <= turn <= hi:
            return name
    return "endgame"


def _load_games(user_id: int | None = None) -> list[dict]:
    if not GAMES_DIR.exists():
        return []
    games = []
    for p in sorted(GAMES_DIR.glob("*.json")):
        if p.name.endswith(".review.json"):
            continue
        try:
            g = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        # Older records have no "user_id" key at all and stay visible.
        if user_id is not None and g.get("user_id", user_id) != user_id:
            continue
        games.append(g)
    return games


def _load_review(sid: str) -> dict | None:
    p = GAMES_DIR / f"{sid}.review.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _game_accuracy(review: dict) -> float | None:
    rows = review.get("results", [])
    if not rows:
        return None
    return sum(max(r["chosen"]["points"], 0) for r in rows) / len(rows)


# --- Form card ---


def _form(ratings, games: list[dict]) -> dict:
    rated = [h for h in ratings.history if h.get("rated") and "user_rating" in h]
    sparkline = [h["user_rating"] for h in rated[-100:]]

    win_loss = None
    if games:
        wins = sum(1 for g in games if g.get("winner") == g.get("human"))
        losses = sum(
            1 for g in games
            if g.get("winner") is not None and g.get("winner") != g.get("human")
        )
        draws = len(games) - wins - losses
        win_loss = {"wins": wins, "losses": losses, "draws": draws, "total": len(games)}

    reviewed = []
    for g in games:
        rev = _load_review(g.get("sid", ""))
        if rev is None:
            continue
        acc = _game_accuracy(rev)
        if acc is not None:
            reviewed.append((g.get("ts", ""), acc))
    reviewed.sort(key=lambda ta: ta[0])
    last10 = [acc for _, acc in reviewed[-10:]]
    review_trend = (
        {"mean": round(sum(last10) / len(last10), 1), "n": len(last10),
         "values": [round(a, 1) for a in last10]}
        if last10 else None
    )

    return {
        "user_rating": round(ratings.user, 1),
        "sparkline": sparkline,
        "games": win_loss,
        "review_accuracy_trend": review_trend,
    }


# --- Skills card ---


def _skills(ratings) -> list[dict]:
    counts: dict[str, int] = {}
    for h in ratings.history:
        if h.get("rated") and h.get("phase"):
            counts[h["phase"]] = counts.get(h["phase"], 0) + 1
    out = []
    for phase in PHASES:
        n = counts.get(phase, 0)
        rating = ratings.skill.get(phase)
        out.append({
            "phase": phase,
            "rating": round(rating, 1) if rating is not None else None,
            "n": n,
            "confident": n >= SKILL_GAP_MIN_N,
        })
    return out


# --- Leaks ---


def _skill_gap_leaks(ratings, skills: list[dict]) -> list[dict]:
    leaks = []
    for s in skills:
        if s["rating"] is None or s["n"] < SKILL_GAP_MIN_N:
            continue
        gap = ratings.user - s["rating"]
        if gap < SKILL_GAP_THRESHOLD:
            continue
        leaks.append({
            "kind": "skill_gap",
            "phase": s["phase"],
            "n": s["n"],
            "phase_rating": s["rating"],
            "overall_rating": round(ratings.user, 1),
            "gap": round(gap, 1),
            "statement": (
                f"{s['phase'].capitalize()} play is your weakest skill "
                f"({s['rating']:.0f} vs {ratings.user:.0f} overall, n={s['n']})."
            ),
            "drill": {"type": "trainer", "phase": s["phase"]},
        })
    leaks.sort(key=lambda l: -l["gap"])
    return leaks


def _taxonomy_leak(games: list[dict]) -> dict | None:
    groups: dict[str, int] = {}
    total = 0
    for g in games:
        rev = _load_review(g.get("sid", ""))
        if rev is None:
            continue
        for row in rev.get("results", []):
            if row.get("verdict") != "blunder":
                continue
            total += 1
            group = _group_for_label(row["chosen"]["label"]) or "other"
            groups[group] = groups.get(group, 0) + 1
    if total < TAXONOMY_MIN_BLUNDERS or not groups:
        return None
    top_group, top_n = max(groups.items(), key=lambda kv: kv[1])
    share = top_n / total
    if share < TAXONOMY_SHARE:
        return None
    return {
        "kind": "review_taxonomy",
        "group": top_group,
        "n": top_n,
        "total": total,
        "share": round(share, 2),
        "statement": f"Reviews: {top_n} of your last {total} blunders were {top_group}.",
        "drill": {"type": "review"},
    }


def _leaks(ratings, skills: list[dict], games: list[dict]) -> list[dict]:
    leaks = _skill_gap_leaks(ratings, skills)
    tax = _taxonomy_leak(games)
    if tax:
        leaks.append(tax)
    # Coach-override and placement-lab-bias leaks aren't available yet
    # (COACH_SPEC / PLACEMENT_LAB_SPEC not built) — nullable by design,
    # they simply don't appear rather than being faked.
    leaks = leaks[:MAX_LEAKS]
    if not leaks:
        return [{"kind": "none", "statement": "No clear leaks yet — keep playing."}]
    return leaks


# --- Cost card ---


def _cost(games: list[dict]) -> dict | None:
    per_game = []
    for g in games:
        rev = _load_review(g.get("sid", ""))
        if rev is None:
            continue
        rows = rev.get("results", [])
        if not rows:
            continue
        by_stage: dict[str, list[float]] = {}
        for row in rows:
            by_stage.setdefault(_stage_for_turn(row["turn"]), []).append(row["regret"])
        per_game.append({
            "ts": g.get("ts", ""),
            "mean_regret": sum(row["regret"] for row in rows) / len(rows),
            "by_stage": {s: sum(v) / len(v) for s, v in by_stage.items()},
        })
    if not per_game:
        return None
    per_game.sort(key=lambda pg: pg["ts"])
    last10 = per_game[-10:]

    stage_sum: dict[str, float] = {}
    stage_n: dict[str, int] = {}
    for pg in last10:
        for s, v in pg["by_stage"].items():
            stage_sum[s] = stage_sum.get(s, 0.0) + v
            stage_n[s] = stage_n.get(s, 0) + 1
    by_stage = {s: round(stage_sum[s] / stage_n[s], 3) for s in stage_sum}

    mean_regret = sum(pg["mean_regret"] for pg in last10) / len(last10)
    trend = None
    if len(last10) >= 4:
        half = len(last10) // 2
        older = sum(pg["mean_regret"] for pg in last10[:half]) / half
        newer = sum(pg["mean_regret"] for pg in last10[half:]) / (len(last10) - half)
        trend = "improving" if newer < older else "worsening" if newer > older else "flat"

    return {
        "mean_regret": round(mean_regret, 3),
        "by_stage": by_stage,
        "n_games": len(last10),
        "trend": trend,
    }


def build(ratings, user_id: int | None = None) -> dict:
    games = _load_games(user_id)
    skills = _skills(ratings)
    return {
        "form": _form(ratings, games),
        "skills": skills,
        "leaks": _leaks(ratings, skills, games),
        "cost": _cost(games),
    }
