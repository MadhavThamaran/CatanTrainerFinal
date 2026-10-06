"""Structured lessons (CURRICULUM_SPEC): the frontmatter loader and its loud
failures, drill resolution, the pass bar and unlock ordering, and — most
important — the Elo-isolation guarantee (CLAUDE.md: "lesson/SRS/lab/review
attempts must never touch the rated puzzle-Elo pool").

Pure pieces run on stand-in puzzles; the service/HTTP tests use the real puzzle
library and the real `content/lessons` (one shared service, a fresh user id per
test, so nothing leaks between tests)."""
from __future__ import annotations

import dataclasses
import itertools
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from test_hosting import _request
from trainer import JsonStore, Ratings, TrainerService, curriculum
from trainer.curriculum import LessonError
from trainer.server import make_handler

LESSONS_DIR = "content/lessons"


# --- stand-in puzzles + lesson text for the pure tests ---


def _fake_puzzles(robber_easy=4, robber_medium=6, robber_hard=3, trade_easy=5):
    out = [SimpleNamespace(id="123456789012", phase="robber", difficulty="easy")]   # a numeric-looking id
    for phase, diff, n in (("robber", "easy", robber_easy), ("robber", "medium", robber_medium),
                           ("robber", "hard", robber_hard), ("trade", "easy", trade_easy)):
        out += [SimpleNamespace(id=f"{phase[0]}{diff[0]}{i:04d}abcdef", phase=phase, difficulty=diff)
                for i in range(n)]
    return out


def _text(**kw):
    f = dict(id="demo-lesson", title="Demo", order=1, pages=2, phase="robber", count=3,
             difficulty="[easy, medium]", pinned="[]", avg=75, min_attempted=3, extra="",
             body="# One\n\nFirst **page**.\n\n---\n# Two\n\nSecond page.")
    f.update(kw)
    return (
        "---\nid: {id}\ntitle: {title}\norder: {order}\npages: {pages}   # a comment\n{extra}"
        "drills:\n  phase: {phase}\n  count: {count}\n  difficulty: {difficulty}\n  pinned: {pinned}\n"
        "pass: {{avg_points: {avg}, min_attempted: {min_attempted}}}\n---\n{body}\n"
    ).format(**f)


def _lesson(puzzles=None, **kw):
    return curriculum.parse_lesson(_text(**kw), "demo.md", puzzles or _fake_puzzles())


# --- loader ---


def test_valid_lesson_parses_comments_flow_collections_and_pages():
    pins = "[123456789012, re0000abcdef]"
    lesson = _lesson(pinned=pins, extra="link: lab\n")
    assert (lesson.id, lesson.title, lesson.order, lesson.link) == ("demo-lesson", "Demo", 1, "lab")
    assert len(lesson.pages) == 2 and lesson.pages[0].startswith("# One")
    assert lesson.drills.phase == "robber" and lesson.drills.count == 3
    assert lesson.drills.difficulty == ("easy", "medium")
    assert lesson.drills.pinned == ("123456789012", "re0000abcdef")   # still strings
    assert (lesson.pass_avg, lesson.pass_min_attempted) == (75.0, 3)
    assert "<strong>page</strong>" in lesson.pages_html[0]


@pytest.mark.parametrize("overrides, message", [
    ({"phase": "sailing"}, "not a puzzle tag"),
    ({"pinned": "[nosuchpuzzle]"}, "is not in the puzzle set"),
    ({"pinned": "[123456789012, 123456789012]"}, "twice"),
    ({"pinned": "[a, b, c, d]"}, "pinned"),
    ({"pages": 3}, "pages: 3 but the body has 2"),
    ({"body": "# One\n\nx\n\n---\n\n---\n# Three"}, "empty page"),
    ({"extra": "colour: blue\n"}, "unknown frontmatter key"),
    ({"difficulty": "[easy, brutal]"}, "must be one of"),
    ({"min_attempted": 9}, "min_attempted must be between"),
    ({"avg": 0}, "avg_points must be in"),
    ({"count": 50}, "only 11 puzzles match"),
    ({"extra": "link: nowhere\n"}, "link 'nowhere'"),
    ({"id": "Not A Slug"}, "lowercase slug"),
    ({"order": "first"}, "order must be a whole number"),
])
def test_loader_rejects_each_authoring_mistake_with_a_clear_message(overrides, message):
    with pytest.raises(LessonError, match=message):
        _lesson(**overrides)


def test_loader_rejects_a_file_without_frontmatter_fences():
    with pytest.raises(LessonError, match="must start with a '---'"):
        curriculum.parse_lesson("# no frontmatter", "x.md", _fake_puzzles())
    with pytest.raises(LessonError, match="never closed"):
        curriculum.parse_lesson("---\nid: a\n", "x.md", _fake_puzzles())


def test_duplicate_ids_and_orders_across_files_are_rejected(tmp_path):
    puzzles = _fake_puzzles()
    (tmp_path / "01-a.md").write_text(_text(id="alpha", order=1), encoding="utf-8")
    (tmp_path / "02-b.md").write_text(_text(id="alpha", order=2), encoding="utf-8")
    with pytest.raises(LessonError, match="duplicate lesson id 'alpha'"):
        curriculum.load_lessons(tmp_path, puzzles)
    (tmp_path / "02-b.md").write_text(_text(id="beta", order=1), encoding="utf-8")
    with pytest.raises(LessonError, match="duplicate order 1"):
        curriculum.load_lessons(tmp_path, puzzles)
    (tmp_path / "02-b.md").write_text(_text(id="beta", order=2), encoding="utf-8")
    assert [l.id for l in curriculum.load_lessons(tmp_path, puzzles)] == ["alpha", "beta"]
    with pytest.raises(LessonError, match="not found"):
        curriculum.load_lessons(tmp_path / "missing", puzzles)


def test_markdown_subset_renders_and_escapes_everything_first():
    html = curriculum.render_markdown(
        "# Title\n\nSome **bold**, *italic* and `6/8 <b>` text <script>alert(1)</script>.\n\n"
        "- one\n- two\n\n1. first\n2. second\n\n## Sub\n\nlast"
    )
    assert "<h3>Title</h3>" in html and "<h4>Sub</h4>" in html
    assert "<strong>bold</strong>" in html and "<em>italic</em>" in html
    assert "<code>6/8 &lt;b&gt;</code>" in html                       # code spans stay literal
    assert "<script>" not in html and "&lt;script&gt;" in html        # no markup injection
    assert "<ul><li>one</li><li>two</li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html
    assert html.endswith("<p>last</p>")


# --- the shipped content ---


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    store = JsonStore(tmp_path_factory.mktemp("lessons") / "state.json")
    return TrainerService("data/puzzles_v5.jsonl", store, lessons_dir=LESSONS_DIR)


_uids = itertools.count(5000)


@pytest.fixture
def uid():
    return next(_uids)


def test_shipped_lessons_load_against_the_live_puzzle_set(service):
    lessons = service.lessons
    assert [l.order for l in lessons] == [1, 2, 3, 4, 5, 6]
    assert [l.drills.phase for l in lessons] == [
        "placement", "placement", "robber", "trade", "devcard", "endgame"]    # spec §1 table
    for l in lessons:
        assert len(l.pages) == 3
        for page in l.pages:
            assert 60 <= len(page.split()) <= 260, (l.id, "page length")
    assert lessons[1].link == "lab"                                           # "The draft"


def test_lessons_teach_this_variant_not_standard_catan(service):
    """CURRICULUM_SPEC §1: an explainer that teaches base-Catan lore is a bug."""
    text = {l.id: " ".join(l.pages).lower() for l in service.lessons}
    everything = " ".join(text.values())
    # Phrases that can only be base-Catan lore (negated true statements, like "you
    # can't trade with the other player", must not be caught, so no trade phrases).
    for wrong in ("10 victory", "10 vp", "more than 7", "7 or more", "domestic trade", "more than 8"):
        assert wrong not in everything, wrong
    assert "more than 9 cards" in text["robber-and-seven"]     # discard limit
    assert "15 victory points" in text["the-race"]
    assert "no player trades" in text["the-draft"] or "can't trade with the other player" in text["port-economics"]


# --- drill resolution ---


def test_drills_are_pinned_first_then_the_band_easier_first():
    lesson = _lesson(pinned="[re0001abcdef, rh0000abcdef]", count=6, difficulty="[easy, medium]")
    ids = curriculum.resolve_drills(lesson, _fake_puzzles(), set(), user_id=7)
    assert ids[:2] == ["re0001abcdef", "rh0000abcdef"]          # author order, even a hard pin
    fill = ids[2:]
    by_id = {p.id: p for p in _fake_puzzles()}
    assert len(ids) == 6 and len(set(ids)) == 6
    assert all(by_id[i].phase == "robber" and by_id[i].difficulty in ("easy", "medium") for i in fill)
    ranks = [curriculum.DIFFICULTIES.index(by_id[i].difficulty) for i in fill]
    assert ranks == sorted(ranks)                                       # easy before medium


def test_rated_seen_puzzles_are_skipped_while_fresh_ones_last():
    lesson = _lesson(count=5)
    puzzles = _fake_puzzles()
    band = [p.id for p in puzzles if p.phase == "robber" and p.difficulty in ("easy", "medium")]
    seen = set(band[:5])                                                # 11 in the band, 5 already rated
    ids = curriculum.resolve_drills(lesson, puzzles, seen, user_id=3)
    assert len(ids) == 5 and not (set(ids) & seen)


def test_seen_puzzles_fill_the_gap_but_the_set_never_leaves_the_band():
    lesson = _lesson(count=5)
    puzzles = _fake_puzzles()
    band = [p.id for p in puzzles if p.phase == "robber" and p.difficulty in ("easy", "medium")]
    seen = set(band[:-2])                                               # only 2 fresh left
    ids = curriculum.resolve_drills(lesson, puzzles, seen, user_id=3)
    assert len(ids) == 5 and set(band[-2:]) <= set(ids)                 # the fresh ones come first
    assert set(ids) <= set(band)                                        # hard / other-phase never used


def test_resolution_is_deterministic_per_user_and_lesson():
    lesson, puzzles = _lesson(count=5), _fake_puzzles()
    a = curriculum.resolve_drills(lesson, puzzles, set(), user_id=1)
    assert a == curriculum.resolve_drills(lesson, puzzles, set(), user_id=1)
    assert a != curriculum.resolve_drills(lesson, puzzles, set(), user_id=2)
    other = _lesson(id="other-lesson", count=5)
    assert a != curriculum.resolve_drills(other, puzzles, set(), user_id=1)


# --- pass bar, runs, unlock order (pure) ---


def test_pass_bar_boundary_exactly_75_passes():
    lesson = _lesson(count=8, min_attempted=8)
    assert curriculum.run_passes([75] * 8, lesson)                      # exactly the bar
    assert curriculum.run_passes([100] * 6 + [0, 0], lesson)            # 600 / 8 = 75.0
    assert not curriculum.run_passes([100] * 6 + [0, -25], lesson)      # 71.9
    assert not curriculum.run_passes([100] * 7, lesson)                 # too few attempts


def _start(lesson, puzzles=None, user_id=1):
    puzzles = puzzles or _fake_puzzles()
    state = {}
    curriculum.ensure_drills(state, lesson, puzzles, set(), user_id)
    return state


def test_a_finished_run_is_scored_and_closed_so_a_retry_reuses_the_set():
    lesson = _lesson(count=3, min_attempted=3)
    state = _start(lesson)
    ids = list(state["drill_ids"])

    def play(points):
        out = None
        for pid, pts in zip(ids, points):
            assert curriculum.next_drill_id(state) == pid               # served in set order
            out = curriculum.record_answer(state, lesson, pid, pts)
        return out

    first = play([100, 40, 10])                                         # avg 50: fail
    assert first == {"attempted": 3, "count": 3, "avg": 50.0, "pass_avg": 75.0, "min_attempted": 3,
                     "complete": True, "passed": False}
    assert state["run"] is None and state["runs"] == 1 and not state.get("passed")
    assert state["last_avg"] == state["best_avg"] == 50.0

    second = play([100, 100, 75])                                       # same set again: avg 91.7
    assert second["passed"] and state["passed"] and state["passed_at"]
    assert state["drill_ids"] == ids and state["runs"] == 2
    assert state["best_avg"] == 91.7

    third = play([0, 0, 0])                                             # a bad retry later
    assert not third["passed"] and state["passed"]                      # passing is sticky
    assert state["last_avg"] == 0.0 and state["best_avg"] == 91.7


def test_a_partial_run_resumes_and_a_resubmit_never_double_counts():
    lesson = _lesson(count=3, min_attempted=3)
    state = _start(lesson)
    ids = state["drill_ids"]
    assert curriculum.next_drill_id(state) == ids[0]
    curriculum.record_answer(state, lesson, ids[0], 100)
    again = curriculum.record_answer(state, lesson, ids[0], 0)          # resubmit: ignored
    assert again["attempted"] == 1 and again["avg"] == 100.0
    assert curriculum.next_drill_id(state) == ids[1]                    # resumes where it left off
    assert curriculum.record_answer({}, lesson, ids[0], 50) is None     # no open run: stale submit


def test_editing_a_lesson_re_resolves_the_stored_set():
    lesson = _lesson(count=3, min_attempted=3)
    puzzles = _fake_puzzles()
    state = _start(lesson, puzzles)
    old = list(state["drill_ids"])
    curriculum.next_drill_id(state)                                     # a run is open
    changed = dataclasses.replace(
        lesson, drills=dataclasses.replace(lesson.drills, count=4), pass_min_attempted=3)
    curriculum.ensure_drills(state, changed, puzzles, set(), 1)
    assert len(state["drill_ids"]) == 4 and state["drill_ids"] != old
    assert state["run"] is None                                         # the old run belonged to the old set
    curriculum.ensure_drills(state, changed, puzzles, set(), 1)         # unchanged lesson: stable
    assert len(state["drill_ids"]) == 4


def test_tracks_unlock_in_order():
    puzzles = _fake_puzzles()
    ls = [_lesson(puzzles, id=f"l{i}", order=i) for i in (1, 2, 3, 4)]
    st = lambda: {l.id: {} for l in ls}                                 # noqa: E731
    lessons = st()
    assert [curriculum.status_for(lessons, ls, l) for l in ls] == ["open", "locked", "locked", "locked"]
    lessons["l1"]["passed"] = True
    assert [curriculum.status_for(lessons, ls, l) for l in ls] == ["passed", "open", "locked", "locked"]
    lessons["l3"]["passed"] = True                                      # tested out of the third
    assert [curriculum.status_for(lessons, ls, l) for l in ls] == ["passed", "open", "passed", "open"]


# --- service: real puzzles, real lessons ---


def _best(p):
    return p.best_codec_id


def _worst(p):
    return min(p.moves, key=lambda m: m.points).codec_id


def _drill(svc, uid, lesson_id, pick):
    """Drill a lesson to the end of the current run; returns the final progress."""
    while True:
        payload = svc.next_lesson_drill(uid, lesson_id)
        p = svc.puzzle_by_id(payload["puzzle_id"])
        res = svc.submit_lesson(p.id, pick(p), uid, lesson_id)
        if res["lesson_progress"]["complete"]:
            return res["lesson_progress"]


def _rating_state(r: Ratings) -> str:
    return json.dumps({"user": r.user, "puzzles": r.puzzles, "history": r.history, "skill": r.skill,
                       "srs": r.srs, "ladder": r.ladder}, sort_keys=True)


def test_new_user_sees_the_first_track_open_and_the_rest_locked(service, uid):
    view = service.lessons_view(uid)
    assert view["total"] == 6 and view["passed"] == 0
    assert [r["status"] for r in view["lessons"]] == ["open"] + ["locked"] * 5
    first = view["lessons"][0]
    assert first["id"] == "production-math" and first["drills"] == 8 and first["pass_avg"] == 75.0
    assert first["page_count"] == 3 and first["in_progress"] is None and first["runs"] == 0
    assert view["lessons"][1]["prev_title"] == "Production math"


def test_lesson_view_has_rendered_pages_and_leaks_no_answers(service, uid):
    view = service.lesson_view(uid, "robber-and-seven")
    assert len(view["pages"]) == 3 and view["pages"][0].startswith("<h3>")
    assert view["drill_spec"] == {"phase": "robber", "count": 8, "difficulty": ["easy", "medium"]}
    blob = json.dumps(view)
    assert '"q"' not in blob and "best_codec_id" not in blob and "drill_ids" not in blob
    assert service.lesson_view(uid, "no-such-lesson") is None


def test_serving_a_drill_is_unrated_and_touches_no_rating_state(service, uid):
    payload = service.next_lesson_drill(uid, "robber-and-seven")
    assert "user_rating" not in payload and "puzzle_rating" not in payload
    assert payload["lesson"] == {"id": "robber-and-seven", "title": "Robber & the seven", "attempted": 0,
                                 "count": 8, "avg": None, "pass_avg": 75.0, "min_attempted": 8}
    assert '"points"' not in json.dumps(payload)
    r = service.ratings_for(uid)
    assert r.puzzles == {} and r.history == [] and r.user == 1500.0     # viewing created no entry


def test_a_run_of_best_moves_passes_and_opens_the_next_track(service, uid):
    progress = _drill(service, uid, "production-math", _best)
    assert progress["complete"] and progress["passed"] and progress["avg"] == 100.0
    view = {r["id"]: r for r in service.lessons_view(uid)["lessons"]}
    assert view["production-math"]["status"] == "passed" and view["production-math"]["best_avg"] == 100.0
    assert view["the-draft"]["status"] == "open" and view["robber-and-seven"]["status"] == "locked"
    assert view["production-math"]["runs"] == 1 and view["production-math"]["in_progress"] is None


def test_a_failed_run_retries_on_the_same_drills_and_can_still_pass(service, uid):
    state = lambda: service.ratings_for(uid).lessons["robber-and-seven"]       # noqa: E731
    failed = _drill(service, uid, "robber-and-seven", _worst)
    assert failed["complete"] and not failed["passed"]
    ids = list(state()["drill_ids"])
    assert state()["runs"] == 1 and not state().get("passed")
    # Retry: the first puzzle served is the first drill of the SAME set.
    assert service.next_lesson_drill(uid, "robber-and-seven")["puzzle_id"] == ids[0]
    passed = _drill(service, uid, "robber-and-seven", _best)
    assert passed["passed"] and state()["drill_ids"] == ids and state()["runs"] == 2


def test_a_half_finished_run_resumes_where_it_stopped(service, uid):
    first = service.next_lesson_drill(uid, "robber-and-seven")
    p = service.puzzle_by_id(first["puzzle_id"])
    service.submit_lesson(p.id, _best(p), uid, "robber-and-seven")
    nxt = service.next_lesson_drill(uid, "robber-and-seven")
    assert nxt["puzzle_id"] != first["puzzle_id"] and nxt["lesson"]["attempted"] == 1
    row = next(r for r in service.lessons_view(uid)["lessons"] if r["id"] == "robber-and-seven")
    assert row["in_progress"] == {"attempted": 1, "count": 8}


def test_testing_out_of_a_locked_track_passes_it_and_opens_the_one_after(service, uid):
    status = lambda: {r["id"]: r["status"] for r in service.lessons_view(uid)["lessons"]}   # noqa: E731
    assert status()["robber-and-seven"] == "locked"
    assert _drill(service, uid, "robber-and-seven", _best)["passed"]
    now = status()
    assert now["robber-and-seven"] == "passed" and now["port-economics"] == "open"
    assert now["the-draft"] == "locked" and now["production-math"] == "open"
    row = next(r for r in service.lessons_view(uid)["lessons"] if r["id"] == "robber-and-seven")
    assert row["tested_out"] is True


def test_lesson_drills_never_touch_ratings_history_skill_or_srs(service, uid):
    """The Elo-isolation guard: a failing run AND a passing run leave every
    rating structure exactly as it was, and nothing lands in the SRS queue."""
    r = service.ratings_for(uid)
    before = _rating_state(r)
    _drill(service, uid, "robber-and-seven", _worst)        # blunders would enqueue SRS items if rated
    _drill(service, uid, "robber-and-seven", _best)
    assert _rating_state(r) == before
    assert r.srs == {} and r.history == [] and r.skill == {} and r.puzzles == {}
    fresh = Ratings(service.store, uid)                     # and nothing different was PERSISTED
    assert _rating_state(fresh) == before
    assert fresh.lessons["robber-and-seven"]["runs"] == 2
    # Drilling a puzzle consumed no rated attempt: the rated trainer still rates its first try.
    pid = fresh.lessons["robber-and-seven"]["drill_ids"][0]
    res = service.submit(pid, service.puzzle_by_id(pid).best_codec_id, uid)
    assert res["rating"]["rated"] is True


def test_submit_lesson_rejects_stray_stale_and_illegal_answers(service, uid):
    assert service.submit_lesson("x", 1, uid, "no-such-lesson") == {"error": "unknown lesson"}
    other = service.puzzles[0].id
    assert "not one of this lesson's drills" in service.submit_lesson(other, 1, uid, "robber-and-seven")["error"]
    assert "robber-and-seven" not in service.ratings_for(uid).lessons          # a bad submit made no state
    payload = service.next_lesson_drill(uid, "robber-and-seven")
    p = service.puzzle_by_id(payload["puzzle_id"])
    assert service.submit_lesson(p.id, 10_000, uid, "robber-and-seven") == {"illegal": True}
    assert service.next_lesson_drill(uid, "robber-and-seven")["lesson"]["attempted"] == 0   # nothing recorded
    _drill(service, uid, "robber-and-seven", _best)                             # finish the run...
    stale = service.submit_lesson(p.id, _best(p), uid, "robber-and-seven")     # ...then a late resubmit
    assert "already finished" in stale["error"]


def test_drills_skip_puzzles_already_rated_but_not_merely_viewed_ones(service, uid):
    lesson = service.lessons[2]                                  # robber, easy+medium
    band = [p for p in service.puzzles if p.phase == "robber" and p.difficulty in ("easy", "medium")]
    rated, viewed = band[:120], band[120:]
    r = service.ratings_for(uid)
    for p in rated:                                              # answered in the rated trainer
        r.puzzles[p.id] = {"rating": 1500.0, "attempts": 1, "best_points": 100}
    for p in viewed:                                             # a view alone makes an entry, attempts 0
        r.puzzles[p.id] = {"rating": 1500.0, "attempts": 0, "best_points": None}
    service.next_lesson_drill(uid, lesson.id)
    ids = set(r.lessons[lesson.id]["drill_ids"])
    assert len(ids) == 8 and not ids & {p.id for p in rated}


def test_lesson_state_is_persisted_in_the_ratings_blob(service, uid):
    _drill(service, uid, "production-math", _best)
    fresh = Ratings(service.store, uid)
    st = fresh.lessons["production-math"]
    assert st["passed"] and st["runs"] == 1 and len(st["drill_ids"]) == 8 and st["run"] is None


# --- HTTP ---


@pytest.fixture
def live(service):
    handler = make_handler(service, play=None, review=None, session_secret="test-secret",
                           secure_cookies=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        t.join(timeout=5)


def _signup(port, name):
    status, _, cookie = _request(port, "POST", "/api/signup", {"name": name, "password": "hunter2pass"})
    assert status == 200
    return cookie


def test_http_lesson_endpoints_require_login(live):
    for path in ("/api/lessons", "/api/lessons/production-math", "/api/lessons/production-math/next"):
        status, data, _ = _request(live, "GET", path)
        assert status == 401 and data["error"] == "unauthenticated"
    status, _, _ = _request(live, "POST", "/api/submit",
                            {"puzzle_id": "x", "codec_id": 1, "lesson": "production-math"})
    assert status == 401


def test_http_lesson_round_trip_is_unrated(live, service):
    cookie = _signup(live, "lessonlearner")
    status, listing, _ = _request(live, "GET", "/api/lessons", cookie=cookie)
    assert status == 200 and listing["total"] == 6 and listing["lab_available"] is False
    status, view, _ = _request(live, "GET", "/api/lessons/robber-and-seven", cookie=cookie)
    assert status == 200 and len(view["pages"]) == 3
    assert _request(live, "GET", "/api/lessons/nope", cookie=cookie)[0] == 404
    assert _request(live, "GET", "/api/lessons/nope/next", cookie=cookie)[0] == 404
    assert _request(live, "GET", "/api/lessons/a/b/c", cookie=cookie)[0] == 404

    status, drill, _ = _request(live, "GET", "/api/lessons/robber-and-seven/next", cookie=cookie)
    assert status == 200 and drill["lesson"]["attempted"] == 0 and "puzzle_rating" not in drill
    best = service.puzzle_by_id(drill["puzzle_id"]).best_codec_id
    status, res, _ = _request(live, "POST", "/api/submit", {
        "puzzle_id": drill["puzzle_id"], "codec_id": best, "lesson": "robber-and-seven"}, cookie=cookie)
    assert status == 200 and res["lesson_progress"]["attempted"] == 1 and "rating" not in res
    assert res["points"] == 100 and res["explanation"]

    both = _request(live, "POST", "/api/submit", {
        "puzzle_id": drill["puzzle_id"], "codec_id": best, "lesson": "robber-and-seven", "srs": True},
        cookie=cookie)
    assert both[0] == 400
    stray = _request(live, "POST", "/api/submit", {
        "puzzle_id": service.puzzles[0].id, "codec_id": 1, "lesson": "robber-and-seven"}, cookie=cookie)
    assert stray[0] == 404
