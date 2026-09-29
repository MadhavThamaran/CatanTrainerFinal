"""Spaced repetition on missed puzzles (SRS_SPEC): a puzzle you failed
comes back at expanding intervals until you own it.

Leitner boxes, not SM-2 — fixed intervals, no per-item ease factors
(overkill for this pool size and harder to test). Pure functions over a
plain dict so `Ratings` (elo.py) can store it in the same per-user blob
it already persists; `now()` is the one clock reference, monkeypatchable
by tests so scheduling logic never calls `datetime.now()` inline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BOX_INTERVAL_DAYS = {1: 1, 2: 3, 3: 7, 4: 16, 5: 35}
MAX_BOX = 5
CAP = 200
PASS_THRESHOLD = 75


def now() -> datetime:
    return datetime.now(timezone.utc)


def _due_at(box: int, from_time: datetime) -> str:
    return (from_time + timedelta(days=BOX_INTERVAL_DAYS[box])).isoformat()


def _active(srs: dict) -> list[str]:
    return [pid for pid in srs if not pid.startswith("_")]


def add(srs: dict, pid: str) -> None:
    """Enqueue a rated miss at box 1 (re-missing an already-queued item
    just resets it — same effect as a review fail)."""
    lapses = srs.get(pid, {}).get("lapses", 0)
    t = now()
    srs[pid] = {
        "box": 1, "due": _due_at(1, t), "lapses": lapses, "added": t.isoformat(),
    }
    _evict_if_over_cap(srs)


def _evict_if_over_cap(srs: dict) -> None:
    active = _active(srs)
    if len(active) <= CAP:
        return
    worst = min(
        active,
        key=lambda pid: (srs[pid]["box"], -srs[pid].get("lapses", 0), srs[pid]["added"]),
    )
    del srs[worst]


def record_review(srs: dict, pid: str, passed: bool) -> dict:
    """Apply a review result. Returns {box_before, box_after, graduated,
    due_next} for the response payload."""
    entry = srs[pid]
    box_before = entry["box"]
    if not passed:
        entry["box"] = 1
        entry["lapses"] = entry.get("lapses", 0) + 1
        entry["due"] = _due_at(1, now())
        return {
            "box_before": box_before, "box_after": 1,
            "graduated": False, "due_next": entry["due"],
        }
    if box_before >= MAX_BOX:
        del srs[pid]
        srs["_graduated"] = srs.get("_graduated", 0) + 1
        return {
            "box_before": box_before, "box_after": None,
            "graduated": True, "due_next": None,
        }
    box_after = box_before + 1
    entry["box"] = box_after
    entry["due"] = _due_at(box_after, now())
    return {
        "box_before": box_before, "box_after": box_after,
        "graduated": False, "due_next": entry["due"],
    }


def due_ids(srs: dict, at: datetime | None = None) -> list[str]:
    """Active items due by `at` (default: now), earliest-due first."""
    at = at or now()
    due = [
        (pid, srs[pid]["due"]) for pid in _active(srs)
        if datetime.fromisoformat(srs[pid]["due"]) <= at
    ]
    due.sort(key=lambda pd: pd[1])
    return [pid for pid, _ in due]


def summary(srs: dict, at: datetime | None = None) -> dict:
    return {
        "due": len(due_ids(srs, at)),
        "active": len(_active(srs)),
        "graduated": srs.get("_graduated", 0),
    }
