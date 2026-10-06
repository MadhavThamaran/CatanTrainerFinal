"""The bot ladder (LADDER_SPEC): rung config resolution, unlock/star/Elo
math on scripted outcomes, rated-vs-casual behavior, and a live PlayService
round trip (ladder state persisted, game records carry rung/rated)."""
from __future__ import annotations

import json

from agents import HeuristicAgent
from search import MCTSEngine

from trainer import JsonStore, Ratings
from trainer import ladder
from trainer.play import PlayService


# --- config resolution ---


def test_every_rung_builds_an_agent():
    for r in ladder.RUNGS:
        bot = ladder.make_bot(r.number, seed=1)
        if r.number == 1:
            assert isinstance(bot, HeuristicAgent)
            assert r.net_path is None
        else:
            assert isinstance(bot, MCTSEngine)


def test_rungs_2_and_3_have_no_net():
    assert ladder.get_rung(2).net_path is None
    assert ladder.get_rung(3).net_path is None


def test_rung_table_reports_provisional_by_default():
    table = ladder.rung_table()
    assert len(table) == 8
    assert all(not row["measured"] for row in table)
    assert table[0]["name"] == "Settler"
    assert table[-1]["name"] == "The Engine"


def test_a_measured_rating_replaces_the_provisional_guess(monkeypatch):
    monkeypatch.setattr(ladder, "_CALIBRATED", {5: 1625.0})
    assert ladder.rating_for(5) == 1625.0
    assert ladder.is_measured(5)
    assert ladder.rating_for(4) == ladder.get_rung(4).provisional_elo   # unmeasured
    assert not ladder.is_measured(4)
    rows = {row["number"]: row for row in ladder.rung_table()}
    assert (rows[5]["elo"], rows[5]["measured"]) == (1625.0, True)
    assert (rows[4]["elo"], rows[4]["measured"]) == (1400.0, False)


def test_play_elo_is_scored_against_the_measured_rating(monkeypatch):
    # A rated win from 1200 is worth K * (1 - E), E = 1/(1 + 10**(gap/400)).
    # Rung 6 is declared 1700: gap 500, E = 0.0532 -> +22.7.
    assert ladder.record_game({}, 6, rated=True, winner="you")["play_elo_after"] == 1222.7
    # Measured at 1300 instead: gap 100, E = 0.3599 -> +15.4.
    monkeypatch.setattr(ladder, "_CALIBRATED", {6: 1300.0})
    assert ladder.record_game({}, 6, rated=True, winner="you")["play_elo_after"] == 1215.4


def test_calibration_file_is_loaded_in_the_shape_the_script_writes(tmp_path, monkeypatch):
    path = tmp_path / "ladder_calibration.json"
    path.write_text(json.dumps({"elo": {"1": 812.5, "3": 1200.0, "8": 2031.4}}))
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", path)
    assert ladder._load_calibration() == {1: 812.5, 3: 1200.0, 8: 2031.4}
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", tmp_path / "missing.json")
    assert ladder._load_calibration() == {}


# --- unlock / star / Elo math (hand-computed fixtures) ---


def test_rungs_1_to_3_start_unlocked_rest_dont():
    state = {}
    for n in (1, 2, 3):
        assert ladder.is_unlocked(state, n)
    for n in (4, 5, 6, 7, 8):
        assert not ladder.is_unlocked(state, n)


def test_first_win_unlocks_the_next_rung_only_once():
    state = {}
    deltas = ladder.record_game(state, 3, rated=True, winner="you")
    assert deltas["unlocked_next"] == 4
    assert ladder.is_unlocked(state, 4)
    # Beating rung 3 again doesn't re-announce the unlock.
    deltas2 = ladder.record_game(state, 3, rated=True, winner="you")
    assert deltas2["unlocked_next"] is None


def test_a_loss_never_unlocks_anything():
    state = {}
    deltas = ladder.record_game(state, 3, rated=True, winner="bot")
    assert deltas["unlocked_next"] is None
    assert not ladder.is_unlocked(state, 4)


def test_stars_hand_computed():
    state = {}
    # 1 win -> *
    ladder.record_game(state, 3, rated=True, winner="you")
    assert state["rungs"]["3"]["stars"] == 1
    # 2 more wins (3 total) -> **
    ladder.record_game(state, 3, rated=True, winner="you")
    ladder.record_game(state, 3, rated=True, winner="you")
    assert state["rungs"]["3"]["stars"] == 2
    # 3 games played so far (all wins). Add 3 more wins + 4 losses -> exactly
    # 10 games total, 6 wins = 60% -> ***
    for _ in range(3):
        ladder.record_game(state, 3, rated=True, winner="you")
    for _ in range(4):
        ladder.record_game(state, 3, rated=True, winner="bot")
    rec = state["rungs"]["3"]
    assert len(rec["last10"]) == 10
    assert sum(rec["last10"]) / 10 == 0.6
    assert rec["stars"] == 3


def test_elo_math_matches_hand_computed_expected_value():
    state = {}
    rung_elo = ladder.rating_for(3)   # 1200 provisional
    expected = 1.0 / (1.0 + 10 ** ((rung_elo - ladder.PLAY_ELO_START) / 400.0))
    deltas = ladder.record_game(state, 3, rated=True, winner="you")
    want = ladder.PLAY_ELO_START + ladder.PLAY_ELO_K * (1.0 - expected)
    assert deltas["play_elo_after"] == round(want, 1)
    assert state["play_elo"] == want


def test_draw_moves_elo_toward_the_rung_rating_not_a_full_win():
    state = {}
    win_deltas = ladder.record_game({}, 6, rated=True, winner="you")
    draw_deltas = ladder.record_game(state, 6, rated=True, winner="draw")
    assert draw_deltas["play_elo_after"] < win_deltas["play_elo_after"]


# --- rated vs casual ---


def test_casual_win_unlocks_but_does_not_move_elo_or_stars():
    state = {}
    deltas = ladder.record_game(state, 3, rated=False, winner="you")
    assert deltas["unlocked_next"] == 4
    assert ladder.is_unlocked(state, 4)
    assert deltas["play_elo_after"] == deltas["play_elo_before"]
    assert state.get("play_elo") is None   # never even touched
    assert "3" not in state.get("rungs", {}) or state["rungs"]["3"]["w"] == 0


def test_casual_loss_does_not_record_a_loss():
    state = {}
    ladder.record_game(state, 3, rated=False, winner="bot")
    assert "rungs" not in state or "3" not in state.get("rungs", {}) or (
        state["rungs"]["3"]["l"] == 0
    )


# --- PlayService round trip: persistence, game records carry rung/rated ---


def _play_out(svc, view, max_steps=400):
    import random

    rng = random.Random(0)
    steps = 0
    while not view["game_over"] and steps < max_steps:
        if view.get("discard"):
            hand = [
                r for r, n in view["context"]["resources"].items() for _ in range(n)
            ]
            rng.shuffle(hand)
            view = svc.act(view["session"], discard=hand[: view["discard"]["count"]])
        else:
            mv = rng.choice(view["moves"])
            view = svc.act(view["session"], codec_id=mv["codec_id"])
        steps += 1
    return view


def test_ladder_state_persists_and_game_record_carries_rung_and_rated(tmp_path, monkeypatch):
    import trainer.play as tp

    monkeypatch.setattr(tp, "_GAMES_DIR", tmp_path / "games")
    store = JsonStore(tmp_path / "state.json")
    ratings_cache: dict = {}

    def ratings_for(user_id):
        r = ratings_cache.get(user_id)
        if r is None:
            r = ratings_cache[user_id] = Ratings(store, user_id)
        return r

    svc = PlayService(ratings_for=ratings_for, default_rung=1)
    view = svc.new_game(user_id=1, seed=301_401, rung=1, rated=True)
    assert view["rung"] == 1
    assert view["rated"] is True

    final = _play_out(svc, view)
    assert final["game_over"]
    assert final["ladder"] is not None
    assert final["ladder"]["rated"] is True

    record = json.loads((tmp_path / "games" / f"{final['session']}.json").read_text())
    assert record["rung"] == 1
    assert record["rated"] is True

    # Persisted to the store: a fresh Ratings load sees the same ladder state.
    reloaded = Ratings(store, 1)
    assert "1" in reloaded.ladder.get("rungs", {})


def test_locked_rung_is_rejected(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    ratings_cache: dict = {}

    def ratings_for(user_id):
        r = ratings_cache.get(user_id)
        if r is None:
            r = ratings_cache[user_id] = Ratings(store, user_id)
        return r

    svc = PlayService(ratings_for=ratings_for, default_rung=1)
    view = svc.new_game(user_id=1, seed=301_402, rung=4)   # never unlocked
    assert view.get("error")


# --- a full tiny-budget game vs rung 1 terminates ---


def test_full_game_vs_rung_one_terminates():
    svc = PlayService(default_rung=1)
    view = svc.new_game(seed=301_403, rung=1)
    final = _play_out(svc, view, max_steps=3000)
    assert final["game_over"]
    assert final["winner"] in ("you", "bot", "draw")
