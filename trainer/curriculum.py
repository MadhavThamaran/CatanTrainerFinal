"""Structured lessons (CURRICULUM_SPEC): ordered tracks, each a short authored
explainer + a curated drill set drawn from the live puzzle library + a pass bar.

Content lives in `content/lessons/<nn>-<slug>.md` (frontmatter + markdown pages
split on `---` rules). This module is the thin loader around it, and — like
`srs.py`/`ladder.py` — pure functions over a plain per-user dict that `Ratings`
persists in the blob it already owns:

    {"<lesson id>": {"drill_ids": [...], "sig": "...",
                     "run": {"answers": {puzzle_id: points}} | None,
                     "runs": 2, "passed": True, "passed_at": "...",
                     "best_avg": 87.5, "last_avg": 62.5, "tested_out": False}}

Lesson drills are UNRATED (CURRICULUM_SPEC §3, CLAUDE.md "Elo isolation"):
nothing here — and nothing `TrainerService.submit_lesson` does with it — touches
puzzle-Elo, the attempt history, per-skill ratings or the SRS queue.

Authoring rule (§1): explainers must describe THIS variant (15 VP, discard above
9 cards, friendly robber, no player trades, balanced dice) — see docs/rules.md.
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DIFFICULTIES = ("easy", "medium", "hard")
_DIFF_RANK = {d: i for i, d in enumerate(DIFFICULTIES)}
KNOWN_LINKS = {"lab"}      # `link: lab` -> "Try it in the Placement lab" on the last page
DEFAULT_COUNT = 8
DEFAULT_PASS_AVG = 75.0    # = a "great" on average; ~6-7 of 8 best-move picks
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TOP_KEYS = {"id", "title", "order", "pages", "drills", "pass", "link"}
_DRILL_KEYS = {"phase", "count", "difficulty", "pinned"}
_PASS_KEYS = {"avg_points", "min_attempted"}


class LessonError(ValueError):
    """A lesson file that cannot be served. Raised while loading — at startup,
    or in the test suite — so a broken lesson never fails quietly in prod."""


@dataclass(frozen=True)
class DrillSpec:
    phase: str
    count: int
    difficulty: tuple[str, ...]
    pinned: tuple[str, ...]


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    order: int
    pages: tuple[str, ...]          # markdown, one per page
    pages_html: tuple[str, ...]     # the same, rendered by `render_markdown`
    drills: DrillSpec
    pass_avg: float
    pass_min_attempted: int
    link: str | None
    source: str                     # file name, for error messages

    @property
    def signature(self) -> str:
        """Identity of the drill SET: editing the lesson's filter or pins
        re-resolves the stored set (a stale one would no longer match)."""
        d = self.drills
        blob = json.dumps([d.phase, d.count, list(d.difficulty), list(d.pinned)])
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


# --- frontmatter (a deliberately tiny YAML subset: no dependency) ---


def _strip_comment(s: str) -> str:
    """Drop a trailing ` # comment` that is not inside quotes."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1].isspace()):
            return s[:i]
    return s


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def _split_flow(inner: str) -> list[str]:
    """Split a flow collection's body on commas that are outside quotes."""
    parts, cur, quote = [], [], None
    for ch in inner:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif ch == ",":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _parse_value(raw: str, where: str):
    """Scalars stay STRINGS (a hex puzzle id like 123456789012 must not turn
    into an int); the schema coerces the fields that are numbers."""
    raw = _strip_comment(raw).strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [_unquote(x) for x in _split_flow(raw[1:-1])]
    if raw.startswith("{") and raw.endswith("}"):
        out = {}
        for item in _split_flow(raw[1:-1]):
            k, sep, v = item.partition(":")
            if not sep:
                raise LessonError(f"{where}: expected 'key: value' inside {{...}}, got {item!r}")
            out[k.strip()] = _unquote(v)
        return out
    return _unquote(raw)


def parse_frontmatter(text: str, name: str) -> tuple[dict, str]:
    """Split `---`-fenced frontmatter from the body. Supports `key: value`,
    flow lists `[a, b]`, flow maps `{k: v}`, one level of indented block map,
    and `#` comments — exactly what CURRICULUM_SPEC §2 uses."""
    lines = text.lstrip("﻿").replace("\r\n", "\n").split("\n")   # tolerate a Windows BOM/CRLF
    if not lines or lines[0].strip() != "---":
        raise LessonError(f"{name}: must start with a '---' frontmatter fence")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise LessonError(f"{name}: frontmatter is never closed with '---'") from None
    data: dict = {}
    block: dict | None = None
    for offset, line in enumerate(lines[1:end]):
        where = f"{name}:{offset + 2}"
        if not _strip_comment(line).strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, rest = line.strip().partition(":")
        if not sep:
            raise LessonError(f"{where}: expected 'key: value', got {line.strip()!r}")
        key = key.strip()
        if indent == 0:
            if not _strip_comment(rest).strip():
                block = data[key] = {}       # nested block follows
            else:
                block = None
                data[key] = _parse_value(rest, where)
        else:
            if block is None:
                raise LessonError(f"{where}: unexpected indentation")
            block[key] = _parse_value(rest, where)
    return data, "\n".join(lines[end + 1:])


# --- markdown subset (headings, bold/italic, lists, inline code) ---

_CODE = re.compile(r"(`[^`]+`)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def _inline(text: str) -> str:
    out = []
    for part in _CODE.split(text):
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(f"<code>{html.escape(part[1:-1], quote=False)}</code>")
        else:
            part = html.escape(part, quote=False)
            part = _BOLD.sub(r"<strong>\1</strong>", part)
            out.append(_ITALIC.sub(r"<em>\1</em>", part))
    return "".join(out)


def render_markdown(md: str) -> str:
    """Render the lesson subset to HTML. Everything is HTML-escaped FIRST, so
    authored text can never inject markup; headings map `#`->h3 (the lesson
    title is the page's h2)."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush_para():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def flush_list():
        nonlocal list_tag
        if items:
            out.append(f"<{list_tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{list_tag}>")
            items.clear()
        list_tag = ""

    for line in md.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_list()
            continue
        if m := _HEADING.match(stripped):
            flush_para()
            flush_list()
            level = len(m.group(1)) + 2
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif m := (_BULLET.match(line) or _NUMBERED.match(line)):
            flush_para()
            tag = "ul" if _BULLET.match(line) else "ol"
            if list_tag and tag != list_tag:
                flush_list()
            list_tag = tag
            items.append(m.group(1))
        else:
            flush_list()
            para.append(stripped)
    flush_para()
    flush_list()
    return "\n".join(out)


# --- loading + validation ---


def _int(v, name: str, what: str) -> int:
    try:
        return int(str(v).strip())
    except ValueError:
        raise LessonError(f"{name}: {what} must be a whole number, got {v!r}") from None


def _number(v, name: str, what: str) -> float:
    try:
        return float(str(v).strip())
    except ValueError:
        raise LessonError(f"{name}: {what} must be a number, got {v!r}") from None


def _split_pages(body: str) -> list[str]:
    pages, cur = [], []
    for line in body.split("\n"):
        if line.strip() == "---":
            pages.append("\n".join(cur).strip())
            cur = []
        else:
            cur.append(line)
    pages.append("\n".join(cur).strip())
    return pages


def parse_lesson(text: str, name: str, puzzles: list) -> Lesson:
    """Parse + validate ONE lesson file against the live puzzle set."""
    fm, body = parse_frontmatter(text, name)
    unknown = set(fm) - _TOP_KEYS
    if unknown:
        raise LessonError(f"{name}: unknown frontmatter key(s) {sorted(unknown)}")
    for req in ("id", "title", "order", "pages", "drills"):
        if req not in fm:
            raise LessonError(f"{name}: missing required frontmatter key {req!r}")
    lesson_id = fm["id"]
    if not isinstance(lesson_id, str) or not _SLUG.match(lesson_id):
        raise LessonError(f"{name}: id must be a lowercase slug like 'robber-and-seven', got {lesson_id!r}")
    order = _int(fm["order"], name, "order")
    if order < 1:
        raise LessonError(f"{name}: order must be >= 1")

    pages = _split_pages(body)
    if any(not p for p in pages):
        raise LessonError(f"{name}: has an empty page (a stray '---' rule?)")
    declared = _int(fm["pages"], name, "pages")
    if declared != len(pages):
        raise LessonError(f"{name}: frontmatter says pages: {declared} but the body has {len(pages)} (split on '---')")

    d = fm["drills"]
    if not isinstance(d, dict):
        raise LessonError(f"{name}: drills must be a mapping (phase/count/difficulty/pinned)")
    bad = set(d) - _DRILL_KEYS
    if bad:
        raise LessonError(f"{name}: unknown drills key(s) {sorted(bad)}")
    if "phase" not in d:
        raise LessonError(f"{name}: drills.phase is required")
    known_phases = {p.phase for p in puzzles}
    phase = d["phase"]
    if phase not in known_phases:
        raise LessonError(f"{name}: drills.phase {phase!r} is not a puzzle tag (known: {sorted(known_phases)})")
    count = _int(d.get("count", DEFAULT_COUNT), name, "drills.count")
    if count < 1:
        raise LessonError(f"{name}: drills.count must be >= 1")
    diffs = d.get("difficulty", ["easy", "medium"])
    diffs = [diffs] if isinstance(diffs, str) else list(diffs)
    for x in diffs:
        if x not in DIFFICULTIES:
            raise LessonError(f"{name}: drills.difficulty {x!r} must be one of {list(DIFFICULTIES)}")
    pinned = d.get("pinned", [])
    pinned = [pinned] if isinstance(pinned, str) else list(pinned)
    by_id = {p.id for p in puzzles}
    for pid in pinned:
        if pid not in by_id:
            raise LessonError(f"{name}: pinned puzzle {pid!r} is not in the puzzle set")
    if len(set(pinned)) != len(pinned):
        raise LessonError(f"{name}: drills.pinned lists a puzzle twice")
    if len(pinned) > count:
        raise LessonError(f"{name}: {len(pinned)} pinned puzzles but drills.count is {count}")
    band = {p.id for p in puzzles if p.phase == phase and p.difficulty in diffs}
    available = len(band | set(pinned))
    if available < count:
        raise LessonError(
            f"{name}: only {available} puzzles match phase={phase!r} difficulty={diffs} "
            f"(+pins), but drills.count is {count}"
        )

    p = fm.get("pass", {})
    if not isinstance(p, dict):
        raise LessonError(f"{name}: pass must be a mapping (avg_points/min_attempted)")
    bad = set(p) - _PASS_KEYS
    if bad:
        raise LessonError(f"{name}: unknown pass key(s) {sorted(bad)}")
    pass_avg = _number(p.get("avg_points", DEFAULT_PASS_AVG), name, "pass.avg_points")
    min_attempted = _int(p.get("min_attempted", count), name, "pass.min_attempted")
    if not 0 < pass_avg <= 100:
        raise LessonError(f"{name}: pass.avg_points must be in (0, 100]")
    if not 1 <= min_attempted <= count:
        raise LessonError(f"{name}: pass.min_attempted must be between 1 and drills.count ({count})")

    link = fm.get("link")
    if link is not None and link not in KNOWN_LINKS:
        raise LessonError(f"{name}: link {link!r} must be one of {sorted(KNOWN_LINKS)}")

    return Lesson(
        id=lesson_id, title=str(fm["title"]), order=order,
        pages=tuple(pages), pages_html=tuple(render_markdown(pg) for pg in pages),
        drills=DrillSpec(phase, count, tuple(diffs), tuple(pinned)),
        pass_avg=pass_avg, pass_min_attempted=min_attempted, link=link, source=name,
    )


def load_lessons(directory: str | Path, puzzles: list) -> list[Lesson]:
    """Every `*.md` in `directory`, validated against the live puzzle set and
    sorted by `order`. Raises `LessonError` on any problem (§2)."""
    directory = Path(directory)
    if not directory.is_dir():
        raise LessonError(f"lessons directory not found: {directory}")
    lessons: list[Lesson] = []
    seen_id: dict[str, str] = {}
    seen_order: dict[int, str] = {}
    for path in sorted(directory.glob("*.md")):
        lesson = parse_lesson(path.read_text(encoding="utf-8"), path.name, puzzles)
        if lesson.id in seen_id:
            raise LessonError(f"{path.name}: duplicate lesson id {lesson.id!r} (also in {seen_id[lesson.id]})")
        if lesson.order in seen_order:
            raise LessonError(f"{path.name}: duplicate order {lesson.order} (also in {seen_order[lesson.order]})")
        seen_id[lesson.id], seen_order[lesson.order] = path.name, path.name
        lessons.append(lesson)
    return sorted(lessons, key=lambda lesson: lesson.order)


# --- drill resolution ---


def _seed(user_id: int, lesson_id: str) -> int:
    return int(hashlib.sha256(f"{user_id}:{lesson_id}".encode()).hexdigest()[:16], 16)


def resolve_drills(lesson: Lesson, puzzles: list, seen_rated: set[str], user_id: int) -> list[str]:
    """The lesson's drill set for one user (§2): pinned puzzles first (author
    order), then the phase/difficulty band filled with puzzles the user has NOT
    already had rated, easier first. Deterministic per (user, lesson). If fresh
    puzzles run out, seen ones fill the rest — repeatability is fine, leaving
    the difficulty band is not."""
    spec = lesson.drills
    pinned = list(spec.pinned)
    pinned_set = set(pinned)
    band = sorted(
        (p for p in puzzles
         if p.phase == spec.phase and p.difficulty in spec.difficulty and p.id not in pinned_set),
        key=lambda p: p.id,
    )
    rng = random.Random(_seed(user_id, lesson.id))
    fresh = [p for p in band if p.id not in seen_rated]
    seen = [p for p in band if p.id in seen_rated]
    rng.shuffle(fresh)
    rng.shuffle(seen)
    fill = (fresh + seen)[: max(spec.count - len(pinned), 0)]
    fill.sort(key=lambda p: _DIFF_RANK[p.difficulty])       # stable: shuffled within a band
    return pinned + [p.id for p in fill]


# --- per-user state (plain dicts, persisted by Ratings) ---


def state_for(lessons: dict, lesson_id: str) -> dict:
    return lessons.setdefault(lesson_id, {})


def ensure_drills(state: dict, lesson: Lesson, puzzles: list, seen_rated: set[str], user_id: int) -> list[str]:
    """The user's persisted drill set, (re)resolved on first use or when the
    lesson's definition — or the puzzle library — changed under it. Retries
    reuse the stored set (§3: repeatability is the point)."""
    known = {p.id for p in puzzles}
    ids = state.get("drill_ids")
    if not ids or state.get("sig") != lesson.signature or any(i not in known for i in ids):
        state["drill_ids"] = resolve_drills(lesson, puzzles, seen_rated, user_id)
        state["sig"] = lesson.signature
        state["run"] = None          # an in-progress run belonged to the old set
    return state["drill_ids"]


def _answers(state: dict) -> dict:
    return (state.get("run") or {}).get("answers", {})


def start_or_resume_run(state: dict) -> dict:
    if not state.get("run"):
        state["run"] = {"answers": {}}
    return state["run"]


def next_drill_id(state: dict) -> str | None:
    """First drill of the current run not yet answered (starting the run if
    none is open). None only if the set is empty."""
    answers = start_or_resume_run(state)["answers"]
    return next((pid for pid in state["drill_ids"] if pid not in answers), None)


def progress_view(state: dict, lesson: Lesson) -> dict:
    """Where the CURRENT run stands (what the drill progress bar shows)."""
    answers = _answers(state)
    n = len(answers)
    return {
        "attempted": n,
        "count": len(state.get("drill_ids") or []) or lesson.drills.count,
        "avg": round(sum(answers.values()) / n, 1) if n else None,
        "pass_avg": lesson.pass_avg,
        "min_attempted": lesson.pass_min_attempted,
    }


def run_passes(points: list[float], lesson: Lesson) -> bool:
    """Pass = average >= the bar over at least `min_attempted` puzzles (§3);
    exactly the bar passes."""
    n = len(points)
    return n >= lesson.pass_min_attempted and sum(points) / n >= lesson.pass_avg - 1e-9


def record_answer(state: dict, lesson: Lesson, pid: str, points: float) -> dict | None:
    """Record one drill answer into the open run (first answer per puzzle only —
    a resubmit can't double-count). When the set is exhausted the run is scored:
    stats updated, `passed` made sticky, the run closed so the next `next` is a
    fresh retry on the SAME set. Returns the `lesson_progress` payload, or None
    when there is no open run (a stale submit after the run finished)."""
    if not state.get("run"):
        return None
    answers = state["run"]["answers"]
    answers.setdefault(pid, points)
    view = progress_view(state, lesson)
    view.update(complete=False, passed=False)
    if len(answers) >= len(state["drill_ids"]):
        values = list(answers.values())
        passed = run_passes(values, lesson)
        avg = sum(values) / len(values)
        state["runs"] = state.get("runs", 0) + 1
        state["last_avg"] = round(avg, 1)
        best = state.get("best_avg")
        state["best_avg"] = round(avg if best is None else max(best, avg), 1)
        if passed and not state.get("passed"):
            state["passed"] = True
            state["passed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["run"] = None
        view.update(complete=True, passed=passed)
    return view


def status_for(lessons: dict, ordered: list[Lesson], lesson: Lesson) -> str:
    """'passed' | 'open' | 'locked' (§3): the first track is always open; the
    next opens on passing the one before it."""
    if lessons.get(lesson.id, {}).get("passed"):
        return "passed"
    i = ordered.index(lesson)
    if i == 0 or lessons.get(ordered[i - 1].id, {}).get("passed"):
        return "open"
    return "locked"
