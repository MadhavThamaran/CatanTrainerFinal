"""The analysis board (ANALYSIS_SPEC): the forced-chance engine seam never
perturbs subsequent randomness, a session's root exactly replays its
source (game record or puzzle), the content-addressed node tree reuses
forced lines but always forks fresh on unforced applies, the 200-node cap
errors cleanly, eval is cached per node, and the revealed panel shows the
true (not resampled) opponent hand."""
from __future__ import annotations

import json
import random

import pytest

from engine import (
    DevCard, GameState, Phase, Resource,
    apply_action, force_next_draw, force_next_roll, force_next_steal,
    legal_actions, new_game,
)

from trainer.analysis import AnalysisService, _revealed
from trainer.play import action_from_dict, action_to_dict

from helpers import give, make_main_state


def _svc(puzzle_by_id=None):
    return AnalysisService(puzzle_by_id=puzzle_by_id, sims=4, dets=1, net_path=None)


# --- engine seam: forced chance never perturbs subsequent randomness ---


def test_force_next_roll_fires_once_then_stream_matches_unforced():
    state = new_game(11)
    baseline_dice = new_game(11).dice.clone()
    force_next_roll(state, 9)
    assert state.dice.next_roll(0) == 9
    assert state.dice.next_roll(0) == baseline_dice.next_roll(0)
    assert state.dice.next_roll(0) == baseline_dice.next_roll(0)


def test_force_next_steal_picks_the_forced_resource_without_consuming_entropy():
    class Fake:
        pass

    baseline = random.Random(7)
    Fake.rng = random.Random(7)
    Fake.rng.setstate(baseline.getstate())

    force_next_steal(Fake, Resource.SHEEP)
    hand = [Resource.WOOD, Resource.SHEEP, Resource.ORE]
    assert Fake.rng.choice(hand) == Resource.SHEEP
    assert Fake.rng.choice(["x", "y", "z"]) == baseline.choice(["x", "y", "z"])


def test_force_next_steal_falls_back_when_resource_absent():
    class Fake:
        pass

    Fake.rng = random.Random(3)
    force_next_steal(Fake, Resource.ORE)
    hand = [Resource.WOOD, Resource.SHEEP]
    assert Fake.rng.choice(hand) in hand


def test_force_next_draw_moves_the_card_to_the_top_preserving_order():
    class Fake:
        pass

    Fake.dev_deck = [DevCard.KNIGHT, DevCard.MONOPOLY, DevCard.KNIGHT, DevCard.VICTORY_POINT]
    force_next_draw(Fake, DevCard.VICTORY_POINT)
    assert Fake.dev_deck.pop() is DevCard.VICTORY_POINT
    assert Fake.dev_deck == [DevCard.KNIGHT, DevCard.MONOPOLY, DevCard.KNIGHT]


def test_force_next_draw_raises_when_card_exhausted():
    class Fake:
        pass

    Fake.dev_deck = [DevCard.KNIGHT]
    with pytest.raises(ValueError):
        force_next_draw(Fake, DevCard.MONOPOLY)


# --- revealed panel: the TRUE opponent hand, not a resample ---


def test_revealed_panel_shows_the_true_opponent_hand():
    state = make_main_state()
    give(state, 1, wheat=2, ore=1)
    rev = _revealed(state, actor=0)
    assert rev["opp_hand"] == {"wheat": 2, "ore": 1}


# --- source fidelity ---


def _write_game_record(path, seed, n_actions=6):
    state = new_game(seed)
    log = []
    rng = random.Random(0)
    for _ in range(n_actions):
        if state.phase is Phase.GAME_OVER:
            break
        a = rng.choice(legal_actions(state))
        log.append({"actor": state.player_to_act(), "action": action_to_dict(a)})
        apply_action(state, a)
    record = {
        "sid": path.stem, "seed": seed, "human": 0, "bot": None, "log": log,
        "winner": state.winner, "final_vp": [state.total_vp(0), state.total_vp(1)],
        "ts": "now",
    }
    path.write_text(json.dumps(record))
    return record


def test_game_source_replays_to_the_exact_decision_index(tmp_path, monkeypatch):
    import trainer.analysis as am

    monkeypatch.setattr(am, "_GAMES_DIR", tmp_path)
    record = _write_game_record(tmp_path / "g1.json", seed=301_501, n_actions=5)

    svc = _svc()
    res = svc.new_session("game", "g1", index=3)
    assert "analysis_id" in res

    expected = new_game(301_501)
    for entry in record["log"][:3]:
        apply_action(expected, action_from_dict(entry["action"]))

    root_state = svc._sessions[res["analysis_id"]].states["root"]
    assert [root_state.total_vp(0), root_state.total_vp(1)] == [
        expected.total_vp(0), expected.total_vp(1),
    ]
    assert root_state.buildings == expected.buildings
    assert root_state.roads == expected.roads


def test_puzzle_source_matches_the_stored_state():
    from puzzles import label_candidate
    from test_puzzles import _near_win_candidate

    puzzle, reason = label_candidate(
        _near_win_candidate(), sims=48, dets=2, min_gap=0.04, seeds=(1, 2)
    )
    assert reason == "admitted"
    svc = _svc(puzzle_by_id={puzzle.id: puzzle}.get)
    res = svc.new_session("puzzle", puzzle.id)
    assert "analysis_id" in res

    expected = GameState.from_dict(puzzle.state)
    root_state = svc._sessions[res["analysis_id"]].states["root"]
    assert [root_state.total_vp(0), root_state.total_vp(1)] == [
        expected.total_vp(0), expected.total_vp(1),
    ]
    assert root_state.buildings == expected.buildings


def test_unknown_source_and_unknown_id_error_cleanly():
    svc = _svc()
    assert "error" in svc.new_session("nonsense", "x")
    assert "error" in svc.new_session("puzzle", "no-such-id", None)


# --- node tree: content-addressed reuse, fresh fork on unforced, node cap ---


def test_forced_line_reuses_the_node_unforced_always_forks(tmp_path, monkeypatch):
    import trainer.analysis as am

    monkeypatch.setattr(am, "_GAMES_DIR", tmp_path)
    _write_game_record(tmp_path / "g2.json", seed=301_502, n_actions=0)
    svc = _svc()
    res = svc.new_session("game", "g2")
    aid = res["analysis_id"]
    mv = res["root"]["moves"][0]

    r1 = svc.apply(aid, "root", mv["codec_id"])
    r2 = svc.apply(aid, "root", mv["codec_id"])
    assert r1["node"]["node"] != r2["node"]["node"]   # unforced: always a fresh fork

    r3 = svc.apply(aid, "root", mv["codec_id"], forced={"roll": 8})
    r4 = svc.apply(aid, "root", mv["codec_id"], forced={"roll": 8})
    assert r3["node"]["node"] == r4["node"]["node"]   # same forced line: reused

    r5 = svc.apply(aid, "root", mv["codec_id"], forced={"roll": 6})
    assert r5["node"]["node"] != r3["node"]["node"]   # different forced value: different node


def test_node_cap_errors_cleanly_instead_of_pruning(tmp_path, monkeypatch):
    import trainer.analysis as am

    monkeypatch.setattr(am, "_GAMES_DIR", tmp_path)
    monkeypatch.setattr(am, "_MAX_NODES", 3)
    _write_game_record(tmp_path / "g3.json", seed=301_503, n_actions=0)
    svc = _svc()
    res = svc.new_session("game", "g3")
    aid = res["analysis_id"]
    node, view = "root", res["root"]
    for i in range(5):
        out = svc.apply(aid, node, view["moves"][0]["codec_id"])
        if "error" in out:
            assert out["error"] == "line too deep, start a new analysis"
            return
        node, view = out["node"]["node"], out["node"]
    raise AssertionError("expected the node cap to trigger within 5 applies")


def test_illegal_codec_id_is_rejected(tmp_path, monkeypatch):
    import trainer.analysis as am

    monkeypatch.setattr(am, "_GAMES_DIR", tmp_path)
    _write_game_record(tmp_path / "g4.json", seed=301_504, n_actions=0)
    svc = _svc()
    res = svc.new_session("game", "g4")
    aid = res["analysis_id"]
    legal = {m["codec_id"] for m in res["root"]["moves"]}
    bad = next(i for i in range(200) if i not in legal)
    out = svc.apply(aid, "root", bad)
    assert out == {"error": "illegal move"}


def test_old_node_still_resolves_after_moving_on(tmp_path, monkeypatch):
    import trainer.analysis as am

    monkeypatch.setattr(am, "_GAMES_DIR", tmp_path)
    _write_game_record(tmp_path / "g5.json", seed=301_505, n_actions=0)
    svc = _svc()
    res = svc.new_session("game", "g5")
    aid = res["analysis_id"]
    mv = res["root"]["moves"][0]
    svc.apply(aid, "root", mv["codec_id"])   # move on from root

    root_eval = svc.eval(aid, "root")        # "jump back": root still resolves
    assert "lines" in root_eval
    assert any(l["codec_id"] == mv["codec_id"] for l in root_eval["lines"])


# --- eval caching ---


def test_eval_is_cached_per_node(tmp_path, monkeypatch):
    import trainer.analysis as am

    monkeypatch.setattr(am, "_GAMES_DIR", tmp_path)
    _write_game_record(tmp_path / "g6.json", seed=301_506, n_actions=0)
    svc = _svc()
    res = svc.new_session("game", "g6")
    aid = res["analysis_id"]
    session = svc._sessions[aid]
    calls = []
    real = session.engine.evaluate
    session.engine.evaluate = lambda *a, **kw: (calls.append(1), real(*a, **kw))[1]

    svc.eval(aid, "root")
    svc.eval(aid, "root")
    assert len(calls) == 1


def test_engine_runs_exact_perfect_info_determinization(tmp_path, monkeypatch):
    import trainer.analysis as am

    monkeypatch.setattr(am, "_GAMES_DIR", tmp_path)
    _write_game_record(tmp_path / "g7.json", seed=301_507, n_actions=0)
    svc = _svc()
    res = svc.new_session("game", "g7")
    session = svc._sessions[res["analysis_id"]]
    assert session.engine.exact is True
