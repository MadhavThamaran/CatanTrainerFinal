"""The weakness dashboard (DASHBOARD_SPEC): per-skill Elo math, statement
gating thresholds, phase-filtered puzzle selection, and graceful
degradation with no game/review data yet."""
from __future__ import annotations

import http.client
import json
import random as _random
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer

import trainer.dashboard as dashboard
from test_trainer import _puzzle_file
from trainer import JsonStore, Ratings, TrainerService
from trainer.elo import INITIAL_USER, PUZZLE_K, USER_K, expected
from trainer.server import make_handler


# --- per-skill Elo math (trainer/elo.py) ---


def test_skill_rating_uses_same_delta_as_global_update(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    entry = r.puzzle_entry("p1", "medium")
    user_before, puzzle_rating = r.user, entry["rating"]
    e = expected(user_before, puzzle_rating)
    delta = 1.0 - e   # points=100 -> s=1.0

    r.record("p1", "medium", 100, phase="robber")

    assert abs(r.user - (user_before + USER_K * delta)) < 1e-9
    assert abs(r.skill["robber"] - (INITIAL_USER + USER_K * delta)) < 1e-9
    assert abs(r.puzzles["p1"]["rating"] - (puzzle_rating - PUZZLE_K * delta)) < 1e-9


def test_no_phase_leaves_skill_untouched(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    r.record("p1", "medium", 100, phase=None)
    assert r.skill == {}


def test_only_rated_first_attempt_updates_skill(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    r.record("p1", "medium", 100, phase="robber")
    after_first = r.skill["robber"]
    r.record("p1", "medium", 0, phase="robber")   # re-attempt: unrated
    assert r.skill["robber"] == after_first


# --- statement gating (skill gap) ---


def _grind(ratings, phase, points, n, prefix):
    for i in range(n):
        ratings.record(f"{prefix}{i}", "medium", points, phase=phase, regret=0.0)


def test_skill_gap_leak_fires_above_threshold_and_min_n(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    _grind(r, "trade", 100, 20, "t")     # strong -> pulls global rating up
    _grind(r, "robber", 0, 20, "r")      # weak -> drags that skill down
    skills = dashboard._skills(r)
    robber = next(s for s in skills if s["phase"] == "robber")
    assert robber["n"] == 20 and robber["confident"]
    leaks = dashboard._skill_gap_leaks(r, skills)
    hit = next((l for l in leaks if l["phase"] == "robber"), None)
    assert hit is not None and hit["kind"] == "skill_gap" and hit["gap"] >= 100.0


def test_skill_gap_leak_suppressed_below_min_n(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    _grind(r, "trade", 100, 20, "t")
    _grind(r, "robber", 0, 10, "r")   # n=10 < SKILL_GAP_MIN_N=15
    skills = dashboard._skills(r)
    leaks = dashboard._skill_gap_leaks(r, skills)
    assert not any(l["phase"] == "robber" for l in leaks)


def test_leaks_fallback_statement_when_nothing_qualifies(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    leaks = dashboard._leaks(r, dashboard._skills(r), [])
    assert leaks == [{"kind": "none", "statement": "No clear leaks yet — keep playing."}]


# --- back-compat: pre-enrichment history entries ---


def test_back_compat_history_missing_fields_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    r.history.append({"pid": "old1", "points": 100, "rated": True})  # no phase/regret/user_rating
    payload = dashboard.build(r)
    assert payload["form"]["sparkline"] == []     # excluded: no user_rating
    assert payload["skills"][0]["n"] == 0          # excluded: no phase
    assert payload["cost"] is None


# --- games/review aggregation (Form, Cost cards) ---


def _write_game(dir_, sid, human=0, winner=0, ts="2026-01-01T00:00:00+00:00", **extra):
    (dir_ / f"{sid}.json").write_text(json.dumps({
        "sid": sid, "seed": 1, "human": human, "bot": None, "log": [],
        "winner": winner, "final_vp": [15, 10], "ts": ts, **extra,
    }))


def _write_review(dir_, sid, rows):
    (dir_ / f"{sid}.review.json").write_text(json.dumps({"total": len(rows), "results": rows}))


def _row(turn, verdict, points, label, regret):
    return {
        "i": 0, "nth_decision": 1, "turn": turn,
        "chosen": {"label": label, "q": 0.5, "points": points},
        "best": {"label": "best move", "q": 0.6},
        "regret": regret, "verdict": verdict, "win_prob": 0.6,
        "board": {}, "marks": [],
    }


def test_form_counts_only_the_requesting_users_games(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    _write_game(tmp_path, "mine", user_id=1)
    _write_game(tmp_path, "theirs", user_id=2)
    _write_game(tmp_path, "older")          # written before records carried a user_id
    r = Ratings(JsonStore(tmp_path / "s.json"), user_id=1)

    assert dashboard.build(r, user_id=1)["form"]["games"]["total"] == 2   # mine + older
    assert dashboard.build(r, user_id=2)["form"]["games"]["total"] == 2   # theirs + older
    assert dashboard.build(r)["form"]["games"]["total"] == 3              # no user: unfiltered


def test_form_games_and_review_trend(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    _write_game(tmp_path, "g1", human=0, winner=0)
    _write_game(tmp_path, "g2", human=0, winner=1)
    _write_review(tmp_path, "g1", [_row(5, "best", 100, "Build road at X", 0.0)])

    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    form = dashboard._form(r, dashboard._load_games())
    assert form["games"] == {"wins": 1, "losses": 1, "draws": 0, "total": 2}
    assert form["review_accuracy_trend"] == {"mean": 100.0, "n": 1, "values": [100.0]}


def test_form_and_cost_degrade_gracefully_with_no_games(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    form = dashboard._form(r, dashboard._load_games())
    assert form["games"] is None
    assert form["review_accuracy_trend"] is None
    assert dashboard._cost(dashboard._load_games()) is None


def test_taxonomy_leak_fires_when_group_share_crosses_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    rows = [_row(10, "blunder", -25, "Build road at X", 0.3) for _ in range(4)]
    rows.append(_row(10, "blunder", -25, "Robber to hexY", 0.3))
    _write_game(tmp_path, "g1")
    _write_review(tmp_path, "g1", rows)
    leak = dashboard._taxonomy_leak(dashboard._load_games())
    assert leak == {
        "kind": "review_taxonomy", "group": "builds", "n": 4, "total": 5, "share": 0.8,
        "statement": "Reviews: 4 of your last 5 blunders were builds.",
        "drill": {"type": "review"},
    }


def test_taxonomy_leak_suppressed_below_min_blunders(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    rows = [_row(10, "blunder", -25, "Build road at X", 0.3) for _ in range(3)]
    _write_game(tmp_path, "g1")
    _write_review(tmp_path, "g1", rows)
    assert dashboard._taxonomy_leak(dashboard._load_games()) is None


def test_cost_card_stage_split_and_trend(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    tses = [f"2026-01-0{i+1}T00:00:00+00:00" for i in range(4)]
    for i, ts in enumerate(tses):
        sid = f"g{i}"
        _write_game(tmp_path, sid, ts=ts)
        regret = 0.3 if i < 2 else 0.05   # first half worse -> "improving"
        _write_review(tmp_path, sid, [
            _row(0, "mistake", 0, "Settle X", regret),
            _row(15, "mistake", 0, "Trade wood", regret),
        ])
    cost = dashboard._cost(dashboard._load_games())
    assert cost["n_games"] == 4
    assert set(cost["by_stage"]) == {"setup", "mid"}
    assert cost["trend"] == "improving"


# --- phase-filtered pick (trainer/elo.py, DASHBOARD_SPEC §3 drill link) ---


@dataclass
class _FakePuzzle:
    id: str
    phase: str
    difficulty: str = "medium"


def test_phase_filter_ignores_placement_mixture(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    puzzles = [
        _FakePuzzle("a", "placement"), _FakePuzzle("b", "robber"),
        _FakePuzzle("c", "robber"), _FakePuzzle("d", "trade"),
    ]
    rng = _random.Random(0)
    for _ in range(30):
        picked = r.pick(puzzles, rng, phase="robber")
        assert picked.phase == "robber"


def test_phase_filter_falls_back_when_phase_absent_instead_of_crashing(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    r = Ratings(store, user_id=1)
    puzzles = [_FakePuzzle("a", "trade")]
    rng = _random.Random(0)
    picked = r.pick(puzzles, rng, phase="robber")   # no robber puzzle exists
    assert picked.id == "a"


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


def test_dashboard_and_phase_filter_http_routes(tmp_path, monkeypatch):
    games_dir = tmp_path / "games"
    games_dir.mkdir()
    monkeypatch.setattr(dashboard, "GAMES_DIR", games_dir)  # separate from state.json below
    path, puzzle = _puzzle_file(tmp_path)   # single puzzle, phase="midgame"
    store = JsonStore(tmp_path / "state.json")
    svc = TrainerService(path, store)
    handler = make_handler(svc, play=None, review=None, session_secret="s", secure_cookies=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, _, cookie = _request(port, "POST", "/api/signup",
                                 {"name": "dashtester", "password": "hunter2pass"})
        status, dash, _ = _request(port, "GET", "/api/dashboard", cookie=cookie)
        assert status == 200
        assert dash["form"]["games"] is None       # no game records yet
        assert dash["leaks"][0]["kind"] == "none"  # nothing qualifies yet

        # phase filter: no "robber" puzzle in this fixture -> falls back,
        # still serves the one real puzzle rather than erroring
        status, next_, _ = _request(port, "GET", "/api/next?phase=robber", cookie=cookie)
        assert status == 200 and next_["puzzle_id"] == puzzle.id
    finally:
        server.shutdown()
