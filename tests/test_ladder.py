"""The bot ladder (LADDER_SPEC): rung config resolution (strength dials), the
measured-rating guard, per-user state migration, unlock/star/Elo math on
scripted outcomes, rated-vs-casual behavior, and a live PlayService round trip
(ladder state persisted, game records carry rung/rated)."""
from __future__ import annotations

import dataclasses
import json

from agents import EpsilonAgent, HeuristicAgent
from search import MCTSEngine

from trainer import JsonStore, Ratings
from trainer import ladder
from trainer.play import PlayService


def _win_from_start(rung_elo: float) -> float:
    """Play-Elo after one rated win from the start rating (hand formula)."""
    e = 1.0 / (1.0 + 10 ** ((rung_elo - ladder.PLAY_ELO_START) / 400.0))
    return round(ladder.PLAY_ELO_START + ladder.PLAY_ELO_K * (1.0 - e), 1)


# --- config resolution ---


def test_every_rung_builds_the_bot_its_config_describes():
    for r in ladder.RUNGS:
        bot = ladder.make_bot(r.number, seed=1)
        assert isinstance(bot, EpsilonAgent) == (r.epsilon > 0)     # a noisy rung wraps its base bot
        base = bot.base if isinstance(bot, EpsilonAgent) else bot
        if isinstance(bot, EpsilonAgent):
            assert bot.epsilon == r.epsilon
        if r.kind == "heuristic":
            assert isinstance(base, HeuristicAgent)
            assert r.net_path is None
        else:
            assert isinstance(base, MCTSEngine)
            assert (base.simulations, base.determinizations) == (r.sims, r.dets)
            assert (base.net is not None) == (r.net_path is not None)


def test_the_rung_table_is_ordered_by_declared_strength():
    assert [r.number for r in ladder.RUNGS] == list(range(1, len(ladder.RUNGS) + 1))
    elos = [r.provisional_elo for r in ladder.RUNGS]
    assert elos == sorted(elos) and len(set(elos)) == len(elos)      # strictly increasing
    assert len({r.name for r in ladder.RUNGS}) == len(ladder.RUNGS)
    assert all(0.0 <= r.epsilon <= 1.0 for r in ladder.RUNGS)
    assert ladder.SLOW_RUNGS <= {r.number for r in ladder.RUNGS}


def test_the_anchor_defines_the_scale_and_is_not_a_rung():
    assert ladder.ANCHOR is ladder.get_rung(ladder.ANCHOR_NUMBER)
    assert ladder.ANCHOR_NUMBER not in {r.number for r in ladder.RUNGS}
    assert ladder.ANCHOR_ELO == 1200.0
    # The v1 "Journeyman": raw (no net) MCTS, 160 sims x 4 dets - what 1200 MEANS.
    a = ladder.ANCHOR
    assert (a.kind, a.net_path, a.sims, a.dets, a.epsilon) == ("mcts", None, 160, 4, 0.0)
    assert isinstance(ladder.make_bot(ladder.ANCHOR_NUMBER, seed=1), MCTSEngine)


def test_rung_table_reports_provisional_by_default():
    table = ladder.rung_table()
    assert len(table) == len(ladder.RUNGS)
    assert all(not row["measured"] for row in table)
    assert [row["elo"] for row in table] == [round(r.provisional_elo, 1) for r in ladder.RUNGS]
    assert table[0]["name"] == ladder.get_rung(1).name
    assert table[-1]["name"] == "The Engine"


def test_a_measured_rating_replaces_the_provisional_guess(monkeypatch):
    monkeypatch.setattr(ladder, "_CALIBRATED", {5: 1625.0})
    assert ladder.rating_for(5) == 1625.0
    assert ladder.is_measured(5)
    assert ladder.rating_for(4) == ladder.get_rung(4).provisional_elo   # unmeasured
    assert not ladder.is_measured(4)
    rows = {row["number"]: row for row in ladder.rung_table()}
    assert (rows[5]["elo"], rows[5]["measured"]) == (1625.0, True)
    assert (rows[4]["elo"], rows[4]["measured"]) == (round(ladder.get_rung(4).provisional_elo, 1), False)


def test_play_elo_is_scored_against_the_measured_rating(monkeypatch):
    # A rated win from the start rating is worth K * (1 - E), E = 1/(1 + 10**(gap/400)).
    declared = ladder.get_rung(6).provisional_elo
    assert ladder.record_game({}, 6, rated=True, winner="you")["play_elo_after"] == _win_from_start(declared)
    # Measured at 1300 instead: gap 100, E = 0.3599 -> +15.4 (hand-computed literal).
    monkeypatch.setattr(ladder, "_CALIBRATED", {6: 1300.0})
    assert _win_from_start(1300.0) == 1215.4
    assert ladder.record_game({}, 6, rated=True, winner="you")["play_elo_after"] == 1215.4


def _calibration_file(tmp_path, elo: dict, rungs=None, **extra):
    """A file shaped like scripts/ladder_calibrate.py writes it."""
    rungs = list(elo) if rungs is None else rungs
    path = tmp_path / "ladder_calibration.json"
    path.write_text(json.dumps({
        "elo": {str(n): e for n, e in elo.items()},
        "config": {str(n): ladder.rung_signature(n) for n in rungs},
        "smoothed": [], **extra,
    }))
    return path


def test_calibration_file_is_loaded_in_the_shape_the_script_writes(tmp_path, monkeypatch):
    path = _calibration_file(tmp_path, {1: 812.5, 3: 1100.0, 8: 1391.4})
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", path)
    assert ladder._load_calibration() == {1: 812.5, 3: 1100.0, 8: 1391.4}
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", tmp_path / "missing.json")
    assert ladder._load_calibration() == {}


def test_a_rating_measured_on_a_different_bot_is_ignored(tmp_path, monkeypatch):
    path = _calibration_file(tmp_path, {1: 812.5, 2: 905.0, 3: 1100.0})
    raw = json.loads(path.read_text())
    raw["config"]["2"] = "stale0000000"                       # rung 2 was a different bot then
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", path)
    assert ladder._load_calibration() == {1: 812.5, 3: 1100.0}


def test_a_file_without_a_config_block_is_stale(tmp_path, monkeypatch):
    path = tmp_path / "ladder_calibration.json"
    path.write_text(json.dumps({"elo": {"1": 1149.4, "3": 1200.0}}))        # the v1 layout
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", path)
    assert ladder._load_calibration() == {}


def test_unknown_rung_numbers_in_a_file_are_dropped(tmp_path, monkeypatch):
    path = _calibration_file(tmp_path, {1: 900.0, 99: 1500.0}, rungs=[1])
    raw = json.loads(path.read_text())
    raw["config"]["99"] = "whatever0000"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", path)
    assert ladder._load_calibration() == {1: 900.0}


def test_the_signature_identifies_the_bot_not_its_name_or_number(monkeypatch):
    base = ladder.get_rung(2)
    monkeypatch.setitem(ladder._BY_NUMBER, 97, dataclasses.replace(base, number=97, name="Twin"))
    assert ladder.rung_signature(97) == ladder.rung_signature(2)
    for change in ({"epsilon": base.epsilon + 0.01}, {"sims": base.sims + 1}, {"dets": base.dets + 1},
                   {"net_path": "checkpoints/other.pt"}, {"kind": "heuristic"}):
        monkeypatch.setitem(ladder._BY_NUMBER, 98, dataclasses.replace(base, number=98, **change))
        assert ladder.rung_signature(98) != ladder.rung_signature(2), change


# --- per-user state survives a re-spec by resetting rung records once ---


def test_old_ladder_state_is_reset_once_when_the_table_changes():
    old = {"play_elo": 1337.0, "rungs": {"6": {"w": 3, "l": 0, "d": 0, "stars": 2,
                                               "unlocked": True, "last10": [1, 1, 1]}}}
    state = ladder.migrate(old)
    assert state is old
    assert state["rungs"] == {} and state["play_elo"] == 1337.0           # records reset, rating kept
    assert state["version"] == ladder.LADDER_VERSION
    ladder.record_game(state, 3, rated=True, winner="you")                 # a record under THIS version...
    before = json.dumps(state, sort_keys=True)
    ladder.migrate(state)
    assert json.dumps(state, sort_keys=True) == before                     # ...survives the next pass


def test_a_brand_new_user_is_only_stamped_with_the_version():
    assert ladder.migrate({}) == {"version": ladder.LADDER_VERSION}


def test_ratings_migrate_the_stored_ladder_when_they_load(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.save_ratings(1, {"user_rating": 1500.0, "ladder": {
        "play_elo": 1250.0, "rungs": {"7": {"w": 5, "l": 1, "d": 0, "stars": 2, "unlocked": True,
                                            "last10": [1] * 5}}}})
    r = Ratings(store, 1)
    assert r.ladder["rungs"] == {} and r.ladder["play_elo"] == 1250.0
    assert r.ladder["version"] == ladder.LADDER_VERSION
    assert ladder.is_unlocked(r.ladder, 1) and not ladder.is_unlocked(r.ladder, 7)


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


def test_ladder_state_persists_and_game_record_carries_rung_and_rated(tmp_path, monkeypatch, fast_rung1):
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


def test_full_game_vs_rung_one_terminates(fast_rung1):
    svc = PlayService(default_rung=1)
    view = svc.new_game(seed=301_403, rung=1)
    final = _play_out(svc, view, max_steps=3000)
    assert final["game_over"]
    assert final["winner"] in ("you", "bot", "draw")


def test_the_real_rung_bots_play_legal_moves_in_a_live_session():
    """The REAL v2 bots work through PlayService: rung 1 is a noisy wrapper around the
    engine, rung 7 the plain engine. The session only ever applies legal actions, so a
    run of random human moves with no error is the proof."""
    for rung in (1, 7):
        svc = PlayService(default_rung=rung)
        view = svc.new_game(seed=301_410 + rung, rung=rung)
        assert view["rung"] == rung
        final = _play_out(svc, view, max_steps=25)
        assert "error" not in final
        assert final["game_over"] or final["moves"] or final.get("discard")   # still a live position
