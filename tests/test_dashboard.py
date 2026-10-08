"""The weakness dashboard (DASHBOARD_SPEC): per-skill Elo math, statement
gating thresholds, phase-filtered puzzle selection, and graceful
degradation with no game/review data yet."""
from __future__ import annotations

import http.client
import json
import random as _random
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

import trainer.dashboard as dashboard
import trainer.lab as lab_module
import trainer.play as tp
from test_trainer import _puzzle_file
from trainer import JsonStore, Ratings, TrainerService, ladder, srs
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


# --- per-rung records (LADDER_SPEC §7) and the SRS line (SRS_SPEC §4) ---


def _games(dir_, spec):
    """[(sid, winner, extra)] -> the loaded records; the human is always seat 0."""
    for sid, winner, extra in spec:
        _write_game(dir_, sid, human=0, winner=winner, **extra)
    return dashboard._load_games()


def test_per_rung_records_count_a_game_under_its_rung_only_on_the_current_table(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    now = {"ladder_version": ladder.LADDER_VERSION}
    games = _games(tmp_path, [
        ("a", 0, {"rung": 3, **now}),                                         # a win on rung 3
        ("b", 1, {"rung": 3, **now}),                                         # a loss on rung 3
        ("c", None, {"rung": 5, **now}),                                      # a draw: no winner
        ("d", 0, {"rung": 3, "ladder_version": ladder.LADDER_VERSION - 1}),   # an older table: rung 3 was another bot
        ("e", 1, {}),                                                         # from before records carried a rung
        ("f", 0, {"rung": 99, **now}),                                        # not a rung on this table
    ])
    assert dashboard._rung_records(games) == [
        {"rung": 3, "name": ladder.get_rung(3).name, "wins": 1, "losses": 1, "draws": 0, "total": 2},
        {"rung": 5, "name": ladder.get_rung(5).name, "wins": 0, "losses": 0, "draws": 1, "total": 1},
        {"rung": None, "name": "Earlier games", "wins": 2, "losses": 1, "draws": 0, "total": 3},
    ]


def test_per_rung_records_are_null_without_games_and_ride_in_the_form_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    r = Ratings(JsonStore(tmp_path / "s.json"), user_id=1)
    assert dashboard._rung_records([]) is None
    assert dashboard.build(r)["form"]["by_rung"] is None
    _write_game(tmp_path, "a", human=0, winner=0, rung=2, ladder_version=ladder.LADDER_VERSION)
    form = dashboard.build(r)["form"]
    assert [row["rung"] for row in form["by_rung"]] == [2]
    assert form["games"] == {"wins": 1, "losses": 0, "draws": 0, "total": 1}   # the overall line is unchanged


def test_srs_counts_line_appears_once_a_puzzle_has_entered_the_queue(tmp_path, monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(srs, "now", lambda: t0)
    r = Ratings(JsonStore(tmp_path / "s.json"), user_id=1)
    assert dashboard._srs_counts(r) is None                  # a fresh account: no empty line
    srs.add(r.srs, "p1")
    srs.add(r.srs, "p2")
    r.srs["_graduated"] = 4
    assert dashboard._srs_counts(r) == {"due": 0, "active": 2, "graduated": 4}
    monkeypatch.setattr(srs, "now", lambda: t0 + timedelta(days=2))          # box 1 comes due after a day
    assert dashboard._srs_counts(r)["due"] == 2
    r.srs.clear()
    r.srs["_graduated"] = 1
    assert dashboard._srs_counts(r) == {"due": 0, "active": 0, "graduated": 1}   # graduated-only still shows
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path / "games")
    assert dashboard.build(r)["form"]["srs"] == {"due": 0, "active": 0, "graduated": 1}


# --- the coach-override leak (DASHBOARD_SPEC §2) ---


def _ev(at, outcome, regret=0.2):
    return {"at": at, "action": {}, "regret": regret, "severity": "mistake", "hint": False, "outcome": outcome}


def _coach_games(dir_, per_game, regret=0.2):
    """One outcome list per game (oldest first) -> records carrying those coach events."""
    for g, outcomes in enumerate(per_game):
        events = [_ev(10 * i, o, regret) for i, o in enumerate(outcomes)]
        _write_game(dir_, f"g{g}", ts=f"2026-02-{g + 1:02d}T00:00:00+00:00", coach_events=events)
    return dashboard._load_games()


def test_coach_override_leak_fires_with_its_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    games = _coach_games(tmp_path, [["confirmed"] * 5 + ["changed"], ["confirmed"] * 3 + ["changed"]])
    leak = dashboard._coach_override_leak(games)
    statement = leak.pop("statement")
    assert leak == {"kind": "coach_override", "n": 10, "overridden": 8, "rate": 0.8,
                    "mean_regret": 0.2, "reviewed": 0, "drill": {"type": "coach"}}
    assert "8 of your last 10" in statement and "20%" in statement


@pytest.mark.parametrize("outcomes,fires", [
    (["confirmed"] * 7 + ["changed"] * 2, False),     # 9 settled warnings: under the n gate
    (["confirmed"] * 8 + ["changed"] * 2, True),      # 10 settled, rate 0.8
    (["confirmed"] * 5 + ["changed"] * 5, False),     # rate 0.5 < 0.6
    (["confirmed"] * 6 + ["changed"] * 4, True),      # rate 0.6: the threshold is inclusive
])
def test_coach_override_gates_on_n_and_rate(tmp_path, monkeypatch, outcomes, fires):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    leak = dashboard._coach_override_leak(_coach_games(tmp_path, [outcomes]))
    assert (leak is not None) == fires


def test_coach_override_gates_on_the_cost_of_the_overridden_moves(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    # Playing on through warnings that cost under the coach's own bar is not a leak...
    assert dashboard._coach_override_leak(_coach_games(tmp_path, [["confirmed"] * 10], regret=0.09)) is None
    # ...and at the bar (inclusive) it is.
    assert dashboard._coach_override_leak(_coach_games(tmp_path, [["confirmed"] * 10], regret=0.10)) is not None


def test_a_warning_left_open_is_neither_heeded_nor_overridden(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    assert dashboard._coach_override_leak(_coach_games(tmp_path, [["confirmed"] * 8 + [None, None]])) is None
    leak = dashboard._coach_override_leak(_coach_games(tmp_path, [["confirmed"] * 8 + ["changed"] * 2 + [None] * 5]))
    assert leak["n"] == 10 and leak["overridden"] == 8       # the five open ones are not in the counts


def test_coach_override_counts_only_the_most_recent_warnings(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    # An old habit (30 overrides) followed by 30 warnings heeded: nothing to report today.
    assert dashboard._coach_override_leak(_coach_games(tmp_path, [["confirmed"] * 30, ["changed"] * 30])) is None
    # The reverse: the habit is current.
    leak = dashboard._coach_override_leak(_coach_games(tmp_path, [["changed"] * 30, ["confirmed"] * 30]))
    assert leak["n"] == 30 and leak["overridden"] == 30


def test_coach_override_prefers_the_reviews_regret_for_the_overridden_move(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    games = _coach_games(tmp_path, [["confirmed"] * 10])                     # the coach's own estimate: 0.2 each

    def review(regrets):                         # rows join the events on log_index == at (= 10 * i)
        _write_review(tmp_path, "g0", [{**_row(5, "good", 40, "Build road at X", g), "log_index": 10 * i}
                                       for i, g in enumerate(regrets)])

    review([0.02] * 10)                          # the deeper review says those moves were fine
    assert dashboard._coach_override_leak(dashboard._load_games()) is None
    review([0.3] * 10)
    leak = dashboard._coach_override_leak(dashboard._load_games())
    assert (leak["mean_regret"], leak["reviewed"]) == (0.3, 10)
    review([0.4] * 5)                            # only 5 of the 10 overridden moves were reviewed
    leak = dashboard._coach_override_leak(dashboard._load_games())
    assert (leak["mean_regret"], leak["reviewed"]) == (0.3, 5)               # (5 * 0.4 + 5 * 0.2) / 10
    # A review written before rows carried log_index can't be joined: the coach's estimate stands.
    _write_review(tmp_path, "g0", [_row(5, "blunder", -25, "x", 0.9)] * 3)
    leak = dashboard._coach_override_leak(dashboard._load_games())
    assert (leak["mean_regret"], leak["reviewed"]) == (0.2, 0)
    assert games                                  # (fixture sanity)


def test_the_coach_regret_floor_is_the_coachs_own_normal_bar():
    assert dashboard.COACH_REGRET_FLOOR == tp.COACH_SEVERITY["normal"]


# --- the lab's biases and the slot-selection policy (DASHBOARD_SPEC §2) ---


def test_lab_leaks_relay_the_labs_statements_verbatim():
    ore = "You overvalue ore-heavy spots."
    second = "Your SECOND pick is the leak — drill boards where the best seats are taken."
    payload = {"n": 20, "statements": [{"kind": "ore_share", "statement": ore},
                                       {"kind": "second_pick", "statement": second}]}
    assert dashboard._lab_leaks(payload) == [
        {"kind": "lab_bias", "bias": "ore_share", "n": 20, "statement": ore, "drill": {"type": "lab"}},
        {"kind": "lab_bias", "bias": "second_pick", "n": 20, "statement": second, "drill": {"type": "lab"}},
    ]


def test_lab_leaks_ignore_the_labs_none_statement_and_a_missing_lab():
    none = {"n": 3, "statements": [{"kind": "none", "statement": "No clear biases yet — keep drilling."}]}
    assert dashboard._lab_leaks(none) == []
    assert dashboard._lab_leaks(None) == []


def test_the_real_lab_payload_flows_into_the_leaks_card(tmp_path, monkeypatch):
    attempts = tmp_path / "attempts.jsonl"
    monkeypatch.setattr(lab_module, "_ATTEMPTS_PATH", attempts)
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path / "games")
    chosen = {"pips": 12, "div": 4, "port": None, "ore_share": 1.0, "spots": 3}     # ore-heavy...
    best = {**chosen, "ore_share": 0.1}                                             # ...and nothing else off
    with attempts.open("w") as f:
        for _ in range(15):                                                         # the lab's n >= 15 gate
            f.write(json.dumps({"user_id": 1, "kind": "settlement", "slot": "A1", "regret": 0.05,
                                "f_chosen": chosen, "f_best": best}) + "\n")
    r = Ratings(JsonStore(tmp_path / "s.json"), user_id=1)
    leaks = dashboard.build(r, user_id=1, lab_stats_for=lab_module.stats_for)["leaks"]
    assert [(l["kind"], l["bias"]) for l in leaks] == [("lab_bias", "ore_share")]
    assert dashboard.build(r, user_id=2, lab_stats_for=lab_module.stats_for)["leaks"][0]["kind"] == "none"
    assert dashboard.build(r, user_id=None, lab_stats_for=lab_module.stats_for)["leaks"][0]["kind"] == "none"


def _leak(kind, tag):
    return {"kind": kind, "tag": tag, "statement": tag}


def test_every_kind_that_fires_gets_a_slot_before_any_kind_gets_a_second():
    candidates = ([_leak("skill_gap", f"sg{i}") for i in range(4)]
                  + [_leak("review_taxonomy", "tax"), _leak("coach_override", "coach"),
                     _leak("lab_bias", "lab0"), _leak("lab_bias", "lab1")])
    assert [l["tag"] for l in dashboard._select_leaks(candidates)] == ["sg0", "tax", "coach", "lab0"]


def test_leftover_slots_go_to_second_statements_and_the_survivors_are_grouped_by_kind():
    three_gaps = [_leak("skill_gap", f"sg{i}") for i in range(3)]
    assert [l["tag"] for l in dashboard._select_leaks(three_gaps + [_leak("lab_bias", "lab0")])] == [
        "sg0", "sg1", "sg2", "lab0"]
    candidates = [_leak("skill_gap", "sg0"), _leak("skill_gap", "sg1"), _leak("review_taxonomy", "tax"),
                  _leak("lab_bias", "lab0"), _leak("lab_bias", "lab1")]
    # rounds: (sg0, tax, lab0) then sg1 fills the 4th slot; shown grouped by kind, not by round
    assert [l["tag"] for l in dashboard._select_leaks(candidates)] == ["sg0", "sg1", "tax", "lab0"]


def test_leaks_combine_every_source_and_fall_back_when_nothing_fires(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    r = Ratings(JsonStore(tmp_path / "s.json"), user_id=1)
    _grind(r, "trade", 100, 20, "t")
    _grind(r, "robber", 0, 20, "r")                                   # one skill gap
    games = _coach_games(tmp_path, [["confirmed"] * 10])              # a coach habit
    lab = {"n": 20, "statements": [{"kind": "pips", "statement": "You leave raw production on the table."}]}
    leaks = dashboard._leaks(r, dashboard._skills(r), games, lab)
    assert [l["kind"] for l in leaks] == ["skill_gap", "coach_override", "lab_bias"]
    empty = Ratings(JsonStore(tmp_path / "s2.json"), user_id=2)
    assert dashboard._leaks(empty, dashboard._skills(empty), [], None)[0]["kind"] == "none"


# --- the review-taxonomy leak's drill links (DASHBOARD_SPEC §3) ---


def _blunder_games(dir_, label, n=5, reviewed=("g1",), unreviewed=()):
    rows = [_row(10, "blunder", -25, label, 0.3) for _ in range(n)]
    for sid in (*reviewed, *unreviewed):
        _write_game(dir_, sid)
    for sid in reviewed:
        _write_review(dir_, sid, rows)
    return dashboard._load_games()


@pytest.mark.parametrize("label,phase", [
    ("Robber to hexY", "robber"), ("Trade 4 wood", "trade"), ("Play Knight", "devcard"),
    ("Buy development card", "devcard"),
    ("Build road at X", None), ("Discard 2 ore", None), ("End turn", None),     # span several tags (or none)
])
def test_taxonomy_leak_offers_a_phase_drill_only_for_groups_that_name_one_puzzle_tag(tmp_path, monkeypatch, label, phase):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    drill = dashboard._taxonomy_leak(_blunder_games(tmp_path, label))["drill"]
    assert drill == ({"type": "review", "phase": phase} if phase else {"type": "review"})


def test_taxonomy_leak_links_the_games_that_have_not_been_reviewed(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "GAMES_DIR", tmp_path)
    games = _blunder_games(tmp_path, "Build road at X", unreviewed=("g2", "g3"))
    assert dashboard._taxonomy_leak(games)["drill"] == {"type": "review", "unreviewed": 2}


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


def test_dashboard_route_relays_the_labs_biases(tmp_path, monkeypatch):
    games_dir = tmp_path / "games"
    games_dir.mkdir()
    monkeypatch.setattr(dashboard, "GAMES_DIR", games_dir)
    path, _ = _puzzle_file(tmp_path)
    svc = TrainerService(path, JsonStore(tmp_path / "state.json"))
    asked = []

    def fake_lab(uid):
        asked.append(uid)
        return {"n": 20, "statements": [{"kind": "pips", "statement": "You leave raw production on the table."}]}

    handler = make_handler(svc, play=None, review=None, session_secret="s", secure_cookies=False,
                           lab_stats_for=fake_lab)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, _, cookie = _request(port, "POST", "/api/signup", {"name": "labtester", "password": "hunter2pass"})
        status, dash, _ = _request(port, "GET", "/api/dashboard", cookie=cookie)
        assert status == 200
        assert [(l["kind"], l["bias"], l["drill"]) for l in dash["leaks"]] == [("lab_bias", "pips", {"type": "lab"})]
        assert len(asked) == 1                      # the lab was asked once, for this user
        assert dash["form"]["by_rung"] is None and dash["form"]["srs"] is None
    finally:
        server.shutdown()
