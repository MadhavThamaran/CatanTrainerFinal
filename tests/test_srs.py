"""Spaced repetition on missed puzzles (SRS_SPEC): Leitner box math, cap
eviction, the enqueue rule, and — most important — the Elo-isolation
guarantee (CLAUDE.md: "lesson/SRS/lab/review attempts must never touch
the rated puzzle-Elo pool")."""
from __future__ import annotations

import http.client
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import trainer.srs as srs
from test_trainer import _puzzle_file
from trainer import JsonStore, TrainerService
from trainer.server import make_handler


def _frozen(monkeypatch, t: datetime) -> None:
    monkeypatch.setattr(srs, "now", lambda: t)


# --- pure box-math (trainer/srs.py) ---


def test_add_enqueues_at_box_1(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _frozen(monkeypatch, t0)
    d = {}
    srs.add(d, "p1")
    assert d["p1"]["box"] == 1
    assert d["p1"]["lapses"] == 0
    assert datetime.fromisoformat(d["p1"]["due"]) == t0 + timedelta(days=1)


def test_record_review_pass_promotes_and_advances_due(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _frozen(monkeypatch, t0)
    d = {}
    srs.add(d, "p1")
    t1 = t0 + timedelta(days=1)
    _frozen(monkeypatch, t1)
    result = srs.record_review(d, "p1", passed=True)
    assert result == {
        "box_before": 1, "box_after": 2, "graduated": False, "due_next": d["p1"]["due"],
    }
    assert d["p1"]["box"] == 2
    assert datetime.fromisoformat(d["p1"]["due"]) == t1 + timedelta(days=3)


def test_record_review_fail_resets_to_box_1_and_increments_lapses(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _frozen(monkeypatch, t0)
    d = {}
    srs.add(d, "p1")
    srs.record_review(d, "p1", passed=True)   # -> box 2
    srs.record_review(d, "p1", passed=True)   # -> box 3
    result = srs.record_review(d, "p1", passed=False)
    assert result["box_before"] == 3 and result["box_after"] == 1
    assert d["p1"]["box"] == 1
    assert d["p1"]["lapses"] == 1


def test_box_5_pass_graduates_and_increments_counter(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _frozen(monkeypatch, t0)
    d = {}
    srs.add(d, "p1")                          # box 1
    for _ in range(3):                        # -> box 2, 3, 4
        srs.record_review(d, "p1", passed=True)
    assert d["p1"]["box"] == 4
    result = srs.record_review(d, "p1", passed=True)   # -> box 5
    assert result["box_after"] == 5 and not result["graduated"]
    result = srs.record_review(d, "p1", passed=True)   # box 5 pass -> graduate
    assert result["graduated"] is True and result["box_after"] is None
    assert "p1" not in d
    assert d["_graduated"] == 1


def test_due_ids_earliest_first_and_excludes_not_yet_due(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d = {}
    _frozen(monkeypatch, t0)
    srs.add(d, "a")
    _frozen(monkeypatch, t0 + timedelta(hours=1))
    srs.add(d, "b")
    assert srs.due_ids(d, at=t0) == []             # neither due yet
    at = t0 + timedelta(days=2)
    assert srs.due_ids(d, at=at) == ["a", "b"]      # both due, "a" due first


def test_summary_counts(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _frozen(monkeypatch, t0)
    d = {}
    srs.add(d, "a")
    srs.add(d, "b")
    assert srs.summary(d, at=t0) == {"due": 0, "active": 2, "graduated": 0}
    assert srs.summary(d, at=t0 + timedelta(days=2)) == {
        "due": 2, "active": 2, "graduated": 0,
    }


def test_cap_evicts_oldest_among_lowest_box(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d = {}
    for i in range(srs.CAP):
        _frozen(monkeypatch, t0 + timedelta(seconds=i))
        srs.add(d, f"p{i}")
    assert len(d) == srs.CAP
    _frozen(monkeypatch, t0 + timedelta(seconds=srs.CAP))
    srs.add(d, "new")
    assert len(d) == srs.CAP
    assert "p0" not in d      # oldest box-1 item evicted
    assert "new" in d


def test_cap_eviction_prefers_most_lapsed_tiebreak(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d = {}
    for i in range(srs.CAP):
        _frozen(monkeypatch, t0 + timedelta(seconds=i))
        srs.add(d, f"p{i}")
    # p50 (added in the middle, NOT the oldest) racks up a lapse
    _frozen(monkeypatch, t0 + timedelta(seconds=srs.CAP + 1))
    srs.record_review(d, "p50", passed=False)
    _frozen(monkeypatch, t0 + timedelta(seconds=srs.CAP + 2))
    srs.add(d, "new2")
    assert "p50" not in d      # most-lapsed wins the tie over merely-oldest p0
    assert "p0" in d
    assert "new2" in d


# --- service-level: enqueue rule + Elo isolation (TrainerService) ---


def _svc(tmp_path):
    path, puzzle = _puzzle_file(tmp_path)
    store = JsonStore(tmp_path / "state.json")
    return TrainerService(path, store), puzzle


def test_rated_miss_enqueues_rated_pass_does_not(tmp_path, monkeypatch):
    svc, puzzle = _svc(tmp_path)
    _frozen(monkeypatch, datetime(2026, 1, 1, tzinfo=timezone.utc))
    worst = min(puzzle.moves, key=lambda m: m.points)
    assert worst.points < srs.PASS_THRESHOLD

    svc.submit(puzzle.id, worst.codec_id, user_id=1)
    assert puzzle.id in svc.ratings_for(1).srs

    svc.submit(puzzle.id, puzzle.best_codec_id, user_id=2)
    assert puzzle.id not in svc.ratings_for(2).srs


def test_srs_submit_never_touches_elo(tmp_path, monkeypatch):
    svc, puzzle = _svc(tmp_path)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _frozen(monkeypatch, t0)
    worst = min(puzzle.moves, key=lambda m: m.points)

    svc.submit(puzzle.id, worst.codec_id, user_id=1)   # rated miss: enqueues
    ratings = svc.ratings_for(1)
    user_before, entry_before = ratings.user, dict(ratings.puzzles[puzzle.id])

    _frozen(monkeypatch, t0 + timedelta(days=2))
    res = svc.submit_srs(puzzle.id, worst.codec_id, user_id=1)
    assert "srs_result" in res and "rating" not in res
    assert ratings.user == user_before
    assert ratings.puzzles[puzzle.id] == entry_before   # untouched, incl. attempts


def test_srs_submit_unknown_to_queue_errors(tmp_path, monkeypatch):
    svc, puzzle = _svc(tmp_path)
    res = svc.submit_srs(puzzle.id, puzzle.best_codec_id, user_id=1)
    assert "error" in res


def test_next_srs_and_summary(tmp_path, monkeypatch):
    svc, puzzle = _svc(tmp_path)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _frozen(monkeypatch, t0)
    assert svc.next_srs(1) is None
    assert svc.srs_summary(1) == {"due": 0, "active": 0, "graduated": 0}

    worst = min(puzzle.moves, key=lambda m: m.points)
    svc.submit(puzzle.id, worst.codec_id, user_id=1)
    assert svc.srs_summary(1) == {"due": 0, "active": 1, "graduated": 0}  # box-1 interval not elapsed

    _frozen(monkeypatch, t0 + timedelta(days=2))
    assert svc.srs_summary(1) == {"due": 1, "active": 1, "graduated": 0}
    due = svc.next_srs(1)
    assert due["srs"] is True and due["puzzle_id"] == puzzle.id


# --- HTTP wiring ---


def _request(port, method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"sid={cookie}"
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    cookie_out = None
    set_cookie = resp.getheader("Set-Cookie")
    if set_cookie and "sid=" in set_cookie:
        cookie_out = set_cookie.split("sid=", 1)[1].split(";", 1)[0]
    conn.close()
    return resp.status, data, cookie_out


def test_srs_http_routes_end_to_end(tmp_path, monkeypatch):
    _frozen(monkeypatch, datetime(2026, 1, 1, tzinfo=timezone.utc))
    svc, puzzle = _svc(tmp_path)
    handler = make_handler(svc, play=None, review=None, session_secret="s", secure_cookies=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, _, cookie = _request(port, "POST", "/api/signup",
                                 {"name": "srstester", "password": "hunter2pass"})
        status, summary, _ = _request(port, "GET", "/api/srs/summary", cookie=cookie)
        assert status == 200 and summary == {"due": 0, "active": 0, "graduated": 0}

        status, _, _ = _request(port, "GET", "/api/srs/next", cookie=cookie)
        assert status == 404   # empty queue -> 404 cleanly

        worst = min(puzzle.moves, key=lambda m: m.points)
        status, sub, _ = _request(
            port, "POST", "/api/submit",
            {"puzzle_id": puzzle.id, "codec_id": worst.codec_id}, cookie=cookie,
        )
        assert status == 200 and sub["rating"]["rated"]

        _frozen(monkeypatch, datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=2))
        status, summary, _ = _request(port, "GET", "/api/srs/summary", cookie=cookie)
        assert summary == {"due": 1, "active": 1, "graduated": 0}

        status, due, _ = _request(port, "GET", "/api/srs/next", cookie=cookie)
        assert status == 200 and due["srs"] is True

        status, res, _ = _request(
            port, "POST", "/api/submit",
            {"puzzle_id": puzzle.id, "codec_id": worst.codec_id, "srs": True},
            cookie=cookie,
        )
        assert status == 200 and "srs_result" in res and "rating" not in res
    finally:
        server.shutdown()
