"""Post-game review (trainer/review.py): every human decision scored
against a deeper search pass, verdict banding, disk caching, and the
replay-integrity guarantee it stands on (REVIEW_SPEC)."""
from __future__ import annotations

import json
import random
import threading
import time

from engine import apply_action, legal_actions, new_game
from net.codec import encode_action
from test_play import _play_out, _svc
from trainer.actions import describe_move
from trainer.review import ReviewService
import trainer.play as tp
import trainer.review as tr


def _play_one_game(tmp_path, monkeypatch, seed: int) -> str:
    monkeypatch.setattr(tp, "_GAMES_DIR", tmp_path)
    monkeypatch.setattr(tr, "_GAMES_DIR", tmp_path)
    svc = _svc()  # heuristic bot (net_path=None), sims=8 -> fast full games
    rng = random.Random(seed)
    view = svc.new_game(seed=seed)
    final = _play_out(svc, view, rng)
    assert final["game_over"]
    return view["session"]


def _drain(review: ReviewService, review_id: str, timeout: float = 30.0) -> dict:
    t0 = time.time()
    while True:
        poll = review.poll(review_id, 0)
        if poll["done"]:
            return poll
        assert time.time() - t0 < timeout, "review did not finish in time"
        time.sleep(0.02)


def test_review_scores_every_human_decision(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_101)
    record = json.loads((tmp_path / f"{sid}.json").read_text())
    human = record["human"]

    review = ReviewService(sims=16, dets=2)
    start = review.start(sid)
    assert "error" not in start
    result = _drain(review, start["review_id"])
    rows = result["results"]

    scorable = tr._count_scorable(record)
    assert start["total"] == scorable
    assert result["total"] == scorable
    assert len(rows) == scorable

    state = new_game(record["seed"])
    i = 0
    for entry in record["log"]:
        action = tp.action_from_dict(entry["action"])
        if entry["actor"] == human and len(legal_actions(state)) > 1:
            row = rows[i]
            cid = encode_action(action)
            expected_label = (
                describe_move(cid, state, human)["label"]
                if cid is not None
                else tp._label(action, state, human)
            )
            assert row["chosen"]["label"] == expected_label
            assert row["regret"] >= 0.0
            assert row["chosen"]["points"] in tr.VERDICT_FOR_POINTS
            assert row["verdict"] == tr.VERDICT_FOR_POINTS[row["chosen"]["points"]]
            assert 0.0 <= row["win_prob"] <= 1.0
            assert row["board"]["hexes"]
            i += 1
        apply_action(state, action)
    assert i == scorable
    # replay-integrity: the worker's own assert already checked this, but
    # confirm from the test side too (REVIEW_SPEC §5's tripwire)
    assert [state.total_vp(0), state.total_vp(1)] == record["final_vp"]


def test_review_caches_to_disk_and_survives_restart(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_102)

    review = ReviewService(sims=16, dets=2)
    start = review.start(sid)
    first = _drain(review, start["review_id"])

    cache_path = tmp_path / f"{sid}.review.json"
    assert cache_path.exists()

    # a fresh ReviewService (simulating a server restart) loads the
    # finished review straight from disk instead of recomputing
    review2 = ReviewService(sims=16, dets=2)
    start2 = review2.start(sid)
    assert start2["total"] == first["total"]
    poll2 = review2.poll(start2["review_id"], 0)
    assert poll2["done"] and poll2["results"] == first["results"]


def test_review_poll_pagination_from_offset(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_103)
    review = ReviewService(sims=16, dets=2)
    start = review.start(sid)
    full = _drain(review, start["review_id"])
    if len(full["results"]) < 2:
        return  # trivially short game: nothing to paginate
    partial = review.poll(start["review_id"], 1)
    assert partial["results"] == full["results"][1:]


def test_review_rejects_concurrent_start(tmp_path, monkeypatch):
    sid1 = _play_one_game(tmp_path, monkeypatch, seed=301_104)
    sid2 = _play_one_game(tmp_path, monkeypatch, seed=301_105)

    review = ReviewService(sims=16, dets=2)
    release = threading.Event()
    real_run = review._run

    def blocking_run(review_id, record):
        release.wait(timeout=5)
        real_run(review_id, record)

    monkeypatch.setattr(review, "_run", blocking_run)

    r1 = review.start(sid1)
    assert "error" not in r1
    r2 = review.start(sid2)
    assert r2.get("status") == 409

    release.set()
    _drain(review, r1["review_id"])


def test_unknown_game_errors():
    review = ReviewService(sims=16, dets=2)
    result = review.start("no-such-game-id")
    assert "error" in result
