"""M7 validation: layout geometry, move decoding, Elo math, and the full
service flow (presentation never leaks answers; submission scores, rates,
and re-rates correctly)."""
import json
import random

import pytest

from engine import GameState, legal_actions
from helpers import give, make_main_state, put_city, put_settlement
from puzzles import label_candidate
from trainer import LAYOUT, Ratings, TrainerService, expected
from trainer.actions import describe_move


# --- layout ---

def test_layout_counts_and_geometry():
    assert len(LAYOUT["hexes"]) == 19
    assert len(LAYOUT["vertices"]) == 54
    assert len(LAYOUT["edges"]) == 72
    xs = [v["x"] for v in LAYOUT["vertices"]]
    ys = [v["y"] for v in LAYOUT["vertices"]]
    assert min(xs) > 0 and min(ys) > 0
    assert max(xs) < LAYOUT["width"] and max(ys) < LAYOUT["height"]
    for h in LAYOUT["hexes"]:
        assert len(h["vertex_ids"]) == 6
        # every hex's vertices sit within ~one hex radius of its center
        for vid in h["vertex_ids"]:
            v = LAYOUT["vertices"][vid]
            assert abs(v["x"] - h["x"]) < 60 and abs(v["y"] - h["y"]) < 60


def test_describe_move_covers_all_legal_actions():
    from net.codec import encode_action

    state = make_main_state()
    put_settlement(state, 0, 0)
    give(state, 0, wood=4, brick=4, sheep=2, wheat=2, ore=2)
    state.players[0].dev_cards  # touch
    for a in legal_actions(state):
        cid = encode_action(a)
        d = describe_move(cid, state, 0)
        assert d["codec_id"] == cid
        assert d["kind"] in ("vertex", "edge", "hex", "button")
        assert d["label"] and d["category"]
        if d["kind"] != "button":
            assert isinstance(d["target"], int)
        if d["category"] == "trade":
            assert d["give"] and d["get"] and d["ratio"] in (2, 3, 4)


# --- elo ---

def test_elo_expected_and_first_attempt_only(tmp_path):
    assert abs(expected(1500, 1500) - 0.5) < 1e-9
    assert expected(1700, 1500) > 0.7

    r = Ratings(tmp_path / "s.json")
    before = r.user
    upd = r.record("p1", "medium", 100)
    assert upd["rated"] and r.user > before          # solved -> rating up
    mid = r.user
    upd2 = r.record("p1", "medium", 100)
    assert not upd2["rated"] and r.user == mid       # re-attempt unrated
    upd3 = r.record("p2", "hard", -25)
    assert upd3["rated"] and r.user < mid            # blunder -> rating down
    # persistence round trip
    r2 = Ratings(tmp_path / "s.json")
    assert abs(r2.user - r.user) < 1e-9
    assert r2.puzzles["p1"]["attempts"] == 2


# --- service (uses a real labeled puzzle) ---

def _puzzle_file(tmp_path):
    from engine import DevCard

    state = make_main_state()
    for v in (0, 10, 20, 30, 40, 43):
        put_city(state, 0, v)
    put_settlement(state, 0, 25)
    state.players[0].dev_cards[DevCard.VICTORY_POINT] = 1
    give(state, 0, ore=3, wheat=2, wood=4)
    for v in (2, 5, 8, 14, 17, 47):
        put_city(state, 1, v)
    put_settlement(state, 1, 35)
    put_settlement(state, 1, 52)
    give(state, 1, ore=3, wheat=2)
    cand = {"state": state.to_dict(), "actor": 0, "phase": "midgame"}
    puzzle, reason = label_candidate(cand, sims=96, dets=2, min_gap=0.04, seeds=(1, 2))
    assert reason == "admitted"
    path = tmp_path / "puzzles.jsonl"
    path.write_text(puzzle.to_json() + "\n")
    return str(path), puzzle


def test_next_puzzle_stable_after_viewing_without_solving(tmp_path):
    # Viewing a puzzle creates a rating entry with best_points=None; the
    # selector must not crash on it (regression: None < 100 TypeError).
    path, _ = _puzzle_file(tmp_path)
    svc = TrainerService(path, str(tmp_path / "state.json"), seed=3)
    for _ in range(10):
        p = svc.next_puzzle()          # view only, never submit
        assert p["puzzle_id"]


def test_service_presentation_hides_answers(tmp_path):
    path, puzzle = _puzzle_file(tmp_path)
    svc = TrainerService(path, str(tmp_path / "state.json"), seed=1)
    payload = svc.next_puzzle()
    assert payload["puzzle_id"] == puzzle.id
    blob = json.dumps(payload)
    assert '"q"' not in blob and '"points"' not in blob
    assert '"best"' not in blob and '"best_codec_id"' not in blob and '"rank"' not in blob
    assert len(payload["moves"]) == len(puzzle.moves)
    # Ordering must not leak the ranking: canonical codec-id order, and the
    # best move must not systematically sit first.
    ids = [m["codec_id"] for m in payload["moves"]]
    assert ids == sorted(ids)
    assert ids != [m.codec_id for m in puzzle.moves]  # ranked order differs here
    assert payload["board"]["hexes"][0]["terrain"]
    assert payload["context"]["my_vp"] == 14
    # perfect-state secrets must not appear
    assert "dev_deck" not in blob


def test_service_submit_scores_and_rates(tmp_path):
    path, puzzle = _puzzle_file(tmp_path)
    svc = TrainerService(path, str(tmp_path / "state.json"), seed=1)
    svc.next_puzzle()
    res = svc.submit(puzzle.id, puzzle.best_codec_id)
    assert res["points"] == 100
    assert res["rating"]["rated"]
    assert res["rating"]["user_after"] > res["rating"]["user_before"]
    best_rows = [m for m in res["table"] if m["best"]]
    assert len(best_rows) == 1 and best_rows[0]["chosen"]
    # resubmission: scored but unrated
    worst = max(res["table"], key=lambda m: m["rank"])
    res2 = svc.submit(puzzle.id, worst["codec_id"])
    assert not res2["rating"]["rated"]
    # An action that isn't legal here is softly rejected (not scored/rated).
    assert svc.submit(puzzle.id, -1) == {"illegal": True}


def _placement_puzzle_file(tmp_path):
    """Label a real setup-placement candidate (with road followup)."""
    from engine import new_game

    state = new_game(7)  # fresh game, first settlement decision
    cand = {"state": state.to_dict(), "actor": 0, "phase": "placement"}
    puzzle, reason = label_candidate(cand, sims=64, dets=2, min_gap=0.0, seeds=(1, 2))
    assert reason == "admitted"
    assert puzzle.followup is not None
    path = tmp_path / "placement.jsonl"
    path.write_text(puzzle.to_json() + "\n")
    return str(path), puzzle


def test_placement_followup_composite_scoring(tmp_path):
    path, puzzle = _placement_puzzle_file(tmp_path)
    fu = puzzle.followup
    assert fu["parent_codec_id"] == puzzle.best_codec_id
    assert 2 <= len(fu["moves"]) <= 3          # setup roads on a fresh vertex
    best_road = fu["best_codec_id"]
    worst_road = fu["moves"][-1]["codec_id"]

    # best settlement + best road -> full credit, road table present
    svc = TrainerService(path, str(tmp_path / "s1.json"))
    res = svc.submit(puzzle.id, puzzle.best_codec_id, best_road)
    assert res["road"]["scored"] and res["points"] == 100
    assert res["rated_points"] == 100
    assert any(m["best"] for m in res["road"]["table"])

    # best settlement + worst road -> averaged down (when roads differ)
    svc2 = TrainerService(path, str(tmp_path / "s2.json"))
    res2 = svc2.submit(puzzle.id, puzzle.best_codec_id, worst_road)
    assert res2["road"]["scored"]
    assert res2["rated_points"] == round((100 + res2["road"]["points"]) / 2)

    # non-best settlement -> road not scored, rated on settlement alone
    other = next(m for m in puzzle.moves if m.codec_id != puzzle.best_codec_id)
    svc3 = TrainerService(path, str(tmp_path / "s3.json"))
    res3 = svc3.submit(puzzle.id, other.codec_id, best_road)
    assert res3["road"] == {"scored": False}
    assert res3["rated_points"] == res3["points"]

    # illegal road for the best settlement -> soft reject
    svc4 = TrainerService(path, str(tmp_path / "s4.json"))
    assert svc4.submit(puzzle.id, puzzle.best_codec_id, 125) in ({"illegal": True},) \
        or fu["moves"][0]["codec_id"] == 125  # (125 could legitimately be legal)


def test_reconstructed_position_matches_moves(tmp_path):
    from net.codec import encode_action

    path, puzzle = _puzzle_file(tmp_path)
    svc = TrainerService(path, str(tmp_path / "state.json"))
    payload = svc.present(puzzle.id)
    state = GameState.from_dict(puzzle.state)
    legal_ids = {encode_action(a) for a in legal_actions(state)}
    assert {m["codec_id"] for m in payload["moves"]} == legal_ids