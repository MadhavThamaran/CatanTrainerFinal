"""The weakness dashboard ("your coach's notebook") — DASHBOARD_SPEC.

Pure aggregation over data other features already produce — no engine
time, no new content. Every statement derives only from stored numbers
with explicit thresholds and minimum-n gates (same honesty contract as
EXPLAIN_SPEC/coach/lab): never invent a weakness.

Game records in `data/games/` carry the player's `user_id`, so the Form and
Cost cards read only the requesting user's games (records written before
that field existed stay visible to everyone — they came from the one local
player); the puzzle-Elo sections (global rating, skills, skill-gap leaks)
are per-user via the `Ratings` passed in. The coach-override leak reads the
records' `coach_events` (joined to cached reviews for a second opinion on the
overridden moves); the placement lab's bias statements are relayed from the
provider `build` is given, not re-derived here.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ladder, srs

GAMES_DIR = Path("data/games")
PHASES = ["placement", "robber", "trade", "devcard", "endgame", "midgame"]

SKILL_GAP_THRESHOLD = 100.0
SKILL_GAP_MIN_N = 15
TAXONOMY_MIN_BLUNDERS = 5
TAXONOMY_SHARE = 0.6
COACH_MIN_INTERJECTIONS = 10   # settled warnings needed before the habit means anything
COACH_OVERRIDE_RATE = 0.6      # played-anyway share that makes it a habit (DASHBOARD_SPEC §2)
COACH_WINDOW = 30              # only the most recent settled warnings count: habits change
# The overridden moves must have cost at least what the coach needs to speak up at all: its
# "normal" severity bar (trainer/play.py::COACH_SEVERITY, pinned equal by a test; copied here so
# this module stays free of the engine imports).
COACH_REGRET_FLOOR = 0.10
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

# The groups that name exactly one puzzle tag, so their leak can offer a filtered trainer drill
# (DASHBOARD_SPEC §3). builds / discards / end-turn timing span several tags (or none): they get no
# phase link rather than a guessed one.
_GROUP_PHASE = {"robber moves": "robber", "trades": "trade", "dev plays": "devcard"}


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


def visible_to(record: dict, user_id: int | None) -> bool:
    """Whose game is this? Records written before they carried a `user_id` came from the one
    local player, so they stay visible to everyone; `user_id=None` means no filtering."""
    return user_id is None or record.get("user_id", user_id) == user_id


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
        if not visible_to(g, user_id):
            continue
        games.append(g)
    return games


def _result(game: dict) -> str:
    """"win" | "loss" | "draw" from the human's seat (a draw is a game with no winner)."""
    if game.get("winner") is None:
        return "draw"
    return "win" if game["winner"] == game.get("human") else "loss"


def _load_review(sid: str) -> dict | None:
    p = GAMES_DIR / f"{sid}.review.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def game_accuracy(review: dict) -> float | None:
    """REVIEW_SPEC §3's header number: mean(max(points, 0)) over the scored decisions."""
    rows = review.get("results", [])
    if not rows:
        return None
    return sum(max(r["chosen"]["points"], 0) for r in rows) / len(rows)


# --- Form card ---


def _rung_records(games: list[dict]) -> list[dict] | None:
    """Per-rung W/L/D (LADDER_SPEC §7). A record counts under its rung only while the rung table
    is the one it was played against (`ladder_version`); records from before the dials re-spec, or
    from before records carried a rung, are pooled as "Earlier games" — their rung numbers
    described different bots (the same reason per-user rung records reset on a table change)."""
    if not games:
        return None
    buckets: dict[int | None, dict] = {}
    for g in games:
        n = g.get("rung") if g.get("ladder_version") == ladder.LADDER_VERSION else None
        if n is not None and not ladder.MIN_RUNG <= n <= ladder.MAX_RUNG:
            n = None
        b = buckets.setdefault(n, {"wins": 0, "losses": 0, "draws": 0})
        b[{"win": "wins", "loss": "losses", "draw": "draws"}[_result(g)]] += 1
    out = []
    for n in sorted(k for k in buckets if k is not None):
        out.append({"rung": n, "name": ladder.get_rung(n).name, **buckets[n]})
    if None in buckets:
        out.append({"rung": None, "name": "Earlier games", **buckets[None]})
    for row in out:
        row["total"] = row["wins"] + row["losses"] + row["draws"]
    return out


def _srs_counts(ratings) -> dict | None:
    """SRS_SPEC §4: the review queue's active / graduated counts (None until a puzzle has ever
    entered the queue, so a fresh account sees no empty line)."""
    s = srs.summary(ratings.srs)
    return s if s["active"] or s["graduated"] else None


def _form(ratings, games: list[dict]) -> dict:
    rated = [h for h in ratings.history if h.get("rated") and "user_rating" in h]
    sparkline = [h["user_rating"] for h in rated[-100:]]

    win_loss = None
    if games:
        results = [_result(g) for g in games]
        win_loss = {
            "wins": results.count("win"), "losses": results.count("loss"),
            "draws": results.count("draw"), "total": len(games),
        }

    reviewed = []
    for g in games:
        rev = _load_review(g.get("sid", ""))
        if rev is None:
            continue
        acc = game_accuracy(rev)
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
        "by_rung": _rung_records(games),
        "review_accuracy_trend": review_trend,
        "srs": _srs_counts(ratings),
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
    unreviewed = 0
    for g in games:
        rev = _load_review(g.get("sid", ""))
        if rev is None:
            unreviewed += 1
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
    # DASHBOARD_SPEC §3: a filtered trainer drill when the group names one puzzle tag, and a
    # link to the unreviewed games when there are any (their blunders are not in these counts).
    drill: dict = {"type": "review"}
    if top_group in _GROUP_PHASE:
        drill["phase"] = _GROUP_PHASE[top_group]
    if unreviewed:
        drill["unreviewed"] = unreviewed
    return {
        "kind": "review_taxonomy",
        "group": top_group,
        "n": top_n,
        "total": total,
        "share": round(share, 2),
        "statement": f"Reviews: {top_n} of your last {total} blunders were {top_group}.",
        "drill": drill,
    }


def _coach_override_leak(games: list[dict]) -> dict | None:
    """DASHBOARD_SPEC §2: the player keeps playing on through coach warnings AND those moves were
    costly. Counted over the most recent COACH_WINDOW *settled* warnings (a warning left open when
    the game ended was neither heeded nor overridden). The cost of an overridden move is the deeper
    review's regret for it when that game was reviewed (events join review rows on `log_index`,
    COACH_SPEC §3) and the coach's own estimate otherwise."""
    settled = []                                     # oldest first
    for g in sorted(games, key=lambda g: g.get("ts", "")):
        events = [e for e in g.get("coach_events") or [] if e.get("outcome") in ("confirmed", "changed")]
        if not events:
            continue
        deep: dict[int, float] = {}
        rev = _load_review(g.get("sid", "")) if any(e["outcome"] == "confirmed" for e in events) else None
        for row in (rev or {}).get("results", []):
            if "log_index" in row:
                deep[row["log_index"]] = row["regret"]
        for e in events:
            if e["outcome"] == "confirmed" and e.get("at") in deep:
                settled.append((True, deep[e["at"]], True))
            else:
                settled.append((e["outcome"] == "confirmed", e.get("regret", 0.0), False))
    settled = settled[-COACH_WINDOW:]
    if len(settled) < COACH_MIN_INTERJECTIONS:
        return None
    overridden = [(regret, deep) for confirmed, regret, deep in settled if confirmed]
    rate = len(overridden) / len(settled)
    if rate < COACH_OVERRIDE_RATE:
        return None
    mean_regret = sum(r for r, _ in overridden) / len(overridden)
    if mean_regret < COACH_REGRET_FLOOR:
        return None
    return {
        "kind": "coach_override",
        "n": len(settled),
        "overridden": len(overridden),
        "rate": round(rate, 2),
        "mean_regret": round(mean_regret, 3),
        "reviewed": sum(1 for _, deep in overridden if deep),
        "statement": (
            f"You played on through {len(overridden)} of your last {len(settled)} coach warnings; "
            f"those moves gave up {mean_regret:.0%} win probability on average."
        ),
        "drill": {"type": "coach"},
    }


def _lab_leaks(stats: dict | None) -> list[dict]:
    """PLACEMENT_LAB_SPEC §4's bias statements, passed through verbatim (DASHBOARD_SPEC §2): the
    lab owns the thresholds and the n >= 15 gate, the dashboard only relays what cleared them."""
    return [
        {"kind": "lab_bias", "bias": st["kind"], "n": stats["n"], "statement": st["statement"],
         "drill": {"type": "lab"}}
        for st in (stats or {}).get("statements", [])
        if st.get("kind") != "none"
    ]


_LEAK_KINDS = ("skill_gap", "review_taxonomy", "coach_override", "lab_bias")


def _select_leaks(candidates: list[dict]) -> list[dict]:
    """At most MAX_LEAKS statements. Every kind that fires gets a slot before any kind gets a
    second one, so a player with four skill gaps still hears about their coach habit or lab bias.
    Within a kind the given order stands (skill gaps arrive biggest-gap first); the survivors are
    shown grouped by kind."""
    ranked = [
        (pos, k, leak)
        for k, kind in enumerate(_LEAK_KINDS)
        for pos, leak in enumerate(l for l in candidates if l["kind"] == kind)
    ]
    ranked = sorted(ranked, key=lambda t: (t[0], t[1]))[:MAX_LEAKS]
    return [leak for _, _, leak in sorted(ranked, key=lambda t: (t[1], t[0]))]


def _leaks(ratings, skills: list[dict], games: list[dict], lab_stats: dict | None = None) -> list[dict]:
    candidates = _skill_gap_leaks(ratings, skills)
    for leak in (_taxonomy_leak(games), _coach_override_leak(games)):
        if leak:
            candidates.append(leak)
    candidates += _lab_leaks(lab_stats)
    leaks = _select_leaks(candidates)
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


def build(ratings, user_id: int | None = None, lab_stats_for=None) -> dict:
    """`lab_stats_for(user_id) -> payload` is the placement lab's own bias statements (None when
    the lab isn't running here): the dashboard relays them rather than re-deriving them."""
    games = _load_games(user_id)
    skills = _skills(ratings)
    lab_stats = lab_stats_for(user_id) if lab_stats_for is not None and user_id is not None else None
    return {
        "form": _form(ratings, games),
        "skills": skills,
        "leaks": _leaks(ratings, skills, games, lab_stats),
        "cost": _cost(games),
    }
