"""Post-game review (trainer/review.py): every human decision scored
against a deeper search pass, verdict banding, disk caching, and the
replay-integrity guarantee it stands on (REVIEW_SPEC)."""
from __future__ import annotations

import json
import os
import random
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from engine import ActionType, apply_action, legal_actions, new_game
from net.codec import encode_action
from test_dashboard import _request
from test_play import _play_out, _svc
from test_trainer import _puzzle_file
from trainer import JsonStore, TrainerService, ladder
from trainer.actions import describe_move
from trainer.review import ReviewService
from trainer.server import make_handler
import trainer.play as tp
import trainer.review as tr

pytestmark = pytest.mark.usefixtures("fast_rung1")   # rung 1 = instant heuristic (see conftest)


def _play_one_game_view(tmp_path, monkeypatch, seed: int) -> tuple[str, dict]:
    """Play a full game with its record going to `tmp_path`: (its id, the live final view)."""
    monkeypatch.setattr(tp, "_GAMES_DIR", tmp_path)
    monkeypatch.setattr(tr, "_GAMES_DIR", tmp_path)
    svc = _svc()  # heuristic bot (net_path=None), sims=8 -> fast full games
    rng = random.Random(seed)
    view = svc.new_game(seed=seed)
    final = _play_out(svc, view, rng)
    assert final["game_over"]
    return view["session"], final


def _play_one_game(tmp_path, monkeypatch, seed: int) -> str:
    return _play_one_game_view(tmp_path, monkeypatch, seed)[0]


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


# --- a review that fails must not wedge the service ---


def test_a_review_that_crashes_reports_it_and_frees_the_service(tmp_path, monkeypatch):
    sid1 = _play_one_game(tmp_path, monkeypatch, seed=301_111)
    sid2 = _play_one_game(tmp_path, monkeypatch, seed=301_112)
    review = ReviewService(sims=16, dets=2)

    def boom(net_path):
        raise RuntimeError("no net today")

    real_net = tr._net
    monkeypatch.setattr(tr, "_net", boom)
    poll = _drain(review, review.start(sid1)["review_id"])
    assert poll["done"] and "RuntimeError: no net today" in poll["failed"]
    assert review.running() is None
    assert not (tmp_path / f"{sid1}.review.json").exists()          # nothing is cached from a failed run

    monkeypatch.setattr(tr, "_net", real_net)
    ok = review.start(sid2)                                          # the single-flight lock was released
    assert "error" not in ok and "failed" not in _drain(review, ok["review_id"])
    retry = review.start(sid1)                                       # and the failed game can just be retried
    assert "failed" not in _drain(review, retry["review_id"])


def test_a_record_that_no_longer_replays_fails_loudly_instead_of_caching(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_113)
    path = tmp_path / f"{sid}.json"
    record = json.loads(path.read_text())
    record["final_vp"] = [record["final_vp"][0] + 1, record["final_vp"][1]]     # as if the engine had changed
    path.write_text(json.dumps(record))
    review = ReviewService(sims=16, dets=2)
    poll = _drain(review, review.start(sid)["review_id"])
    assert "replay-integrity check failed" in poll["failed"]
    assert not (tmp_path / f"{sid}.review.json").exists()
    assert review.running() is None


def test_starting_the_review_that_is_running_joins_it(tmp_path, monkeypatch):
    sid1 = _play_one_game(tmp_path, monkeypatch, seed=301_114)
    sid2 = _play_one_game(tmp_path, monkeypatch, seed=301_115)
    review = ReviewService(sims=16, dets=2)
    release = threading.Event()
    real_run = review._run

    def blocking_run(review_id, record):
        release.wait(timeout=5)
        real_run(review_id, record)

    monkeypatch.setattr(review, "_run", blocking_run)
    first = review.start(sid1)
    assert review.running() == sid1
    assert review.start(sid1) == first                  # a page reload mid-review: joined, not refused
    assert review.start(sid2).get("status") == 409      # a different game still has to wait
    release.set()
    _drain(review, sid1)
    assert review.running() is None


@pytest.mark.parametrize("junk", [
    '{"total": 3, "resul',                       # a write that was cut short
    "{}", "[]", "null", '{"total": 3}', '{"results": "oops", "total": 1}',      # valid JSON, not a review
])
def test_a_torn_or_misshapen_review_cache_is_recomputed_not_trusted(tmp_path, monkeypatch, junk):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_116)
    (tmp_path / f"{sid}.review.json").write_text(junk)
    review = ReviewService(sims=16, dets=2)
    assert review.list_games(None)[0]["reviewed"] is False
    poll = _drain(review, review.start(sid)["review_id"])
    assert "failed" not in poll and poll["results"]
    assert json.loads((tmp_path / f"{sid}.review.json").read_text())["total"] == poll["total"]
    assert not list(tmp_path.glob("*.tmp"))                                   # the temp file was published


def test_a_crash_between_writing_and_publishing_the_cache_leaves_no_torn_cache(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_123)
    review = ReviewService(sims=16, dets=2)

    def crash(tmp, cache):
        raise OSError("disk went away")

    real_publish = review._publish
    monkeypatch.setattr(review, "_publish", crash)
    poll = _drain(review, review.start(sid)["review_id"])
    assert "OSError: disk went away" in poll["failed"]
    assert not (tmp_path / f"{sid}.review.json").exists()           # that path only ever holds a whole review
    # (not monkeypatch.undo(): that would also undo the fixtures that keep records out of data/games/)
    monkeypatch.setattr(review, "_publish", real_publish)
    assert "failed" not in _drain(review, review.start(sid)["review_id"])        # and a retry works


def test_a_finished_review_is_never_reported_as_running():
    review = ReviewService()
    review._running, review._done["g1"] = "g1", True       # a worker has flagged done but not yet freed the slot
    assert review.running() is None
    review._done["g1"] = False
    assert review.running() == "g1"


@pytest.mark.parametrize("bad", ["../secret", "a/b", "..", "", "x" * 65, "g1.json", "a b", None, 12, ["g1"]])
def test_game_ids_that_are_not_game_ids_never_reach_the_disk(tmp_path, monkeypatch, bad):
    games = tmp_path / "games"
    games.mkdir()
    monkeypatch.setattr(tr, "_GAMES_DIR", games)
    # a valid-looking record where "../secret" would resolve to
    (tmp_path / "secret.json").write_text(json.dumps({"seed": 1, "human": 0, "log": [], "final_vp": [0, 0]}))
    (tmp_path / "secret.review.json").write_text(json.dumps({"total": 1, "results": []}))   # start() reads this first
    review = ReviewService(sims=16, dets=2)
    assert review.start(bad) == {"error": "unknown game"}
    assert review.open_game(bad, None) == {"error": "unknown game"}


# --- the player's recent games (REVIEW_SPEC §6) ---

_MISSING = object()


def _record(games_dir, sid, *, ts, human=0, winner=0, final_vp=(15, 9), rung=3, rated=False,
            user_id=1, **extra):
    """A minimal finished-game record (enough for the list, not for a replay)."""
    record = {"sid": sid, "seed": 1, "human": human, "bot": None, "rung": rung, "rated": rated,
              "ladder_version": ladder.LADDER_VERSION, "log": [], "coach_events": [], "winner": winner,
              "final_vp": list(final_vp), "ts": ts, **extra}
    if user_id is not _MISSING:
        record["user_id"] = user_id
    (games_dir / f"{sid}.json").write_text(json.dumps(record))


@pytest.fixture
def games(tmp_path, monkeypatch):
    d = tmp_path / "games"
    d.mkdir()
    monkeypatch.setattr(tr, "_GAMES_DIR", d)
    return d


def test_list_games_is_newest_first_oriented_to_the_player_and_marks_reviewed_games(games):
    _record(games, "old", ts="2026-03-01T10:00:00+00:00", winner=1, final_vp=(8, 15))              # a loss
    _record(games, "mid", ts="2026-03-02T10:00:00+00:00", human=1, winner=1, final_vp=(9, 15),
            rung=5, rated=True)                                                                    # a win from seat 1
    _record(games, "new", ts="2026-03-03T10:00:00+00:00", winner=None, final_vp=(10, 10))          # a draw
    (games / "mid.review.json").write_text(json.dumps({"total": 2, "results": [
        {"chosen": {"points": 100}}, {"chosen": {"points": -25}}]}))                                # (100 + 0) / 2
    for name, mtime in (("old", 3000), ("mid", 2000), ("new", 1000)):
        os.utime(games / f"{name}.json", (mtime, mtime))      # file times disagree with the records' own ts
    new, mid, old = ReviewService().list_games(1)
    assert [g["id"] for g in (new, mid, old)] == ["new", "mid", "old"]
    assert (old["winner"], old["final_vp"]) == ("bot", {"you": 8, "bot": 15})
    assert (mid["winner"], mid["final_vp"], mid["rated"]) == ("you", {"you": 15, "bot": 9}, True)
    assert (mid["rung"], mid["rung_name"]) == (5, ladder.get_rung(5).name)
    assert new["winner"] == "draw"
    assert (mid["reviewed"], mid["accuracy"]) == (True, 50.0)
    assert (old["reviewed"], old["accuracy"]) == (False, None)


def test_list_games_shows_only_the_players_games_plus_older_untagged_records(games):
    _record(games, "mine", ts="2026-03-01T00:00:00+00:00", user_id=1)
    _record(games, "theirs", ts="2026-03-02T00:00:00+00:00", user_id=2)
    _record(games, "older", ts="2026-03-03T00:00:00+00:00", user_id=_MISSING)     # written before user_id existed
    review = ReviewService()
    assert {g["id"] for g in review.list_games(1)} == {"mine", "older"}
    assert {g["id"] for g in review.list_games(2)} == {"theirs", "older"}


def test_list_games_caps_at_the_most_recent_and_skips_what_is_not_a_game(games):
    for i in range(5):
        _record(games, f"g{i}", ts=f"2026-03-0{i + 1}T00:00:00+00:00")
        os.utime(games / f"g{i}.json", (1000 + i, 1000 + i))
    (games / "junk.json").write_text("not json")
    (games / "other.json").write_text(json.dumps({"hello": 1}))
    for name in ("junk.json", "other.json"):
        os.utime(games / name, (5000, 5000))                  # the newest files: skipping them must not eat the cap
    review = ReviewService()
    assert [g["id"] for g in review.list_games(1, limit=3)] == ["g4", "g3", "g2"]
    assert len(review.list_games(1)) == 5


def test_list_games_gives_a_record_from_an_older_ladder_no_rung(games):
    _record(games, "v1", ts="2026-03-01T00:00:00+00:00", rung=6, ladder_version=ladder.LADDER_VERSION - 1)
    _record(games, "none", ts="2026-03-02T00:00:00+00:00", rung=None)
    for g in ReviewService().list_games(1):
        assert (g["rung"], g["rung_name"]) == (None, "Earlier game")


def test_list_games_with_no_games_directory_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "_GAMES_DIR", tmp_path / "nowhere")
    assert ReviewService().list_games(1) == []


# --- reopening a finished game ---


def test_open_game_rebuilds_the_finished_play_screen(tmp_path, monkeypatch):
    sid, live = _play_one_game_view(tmp_path, monkeypatch, seed=301_117)
    record = json.loads((tmp_path / f"{sid}.json").read_text())
    view = ReviewService().open_game(sid, None)
    assert "error" not in view
    assert set(view) - {"reviewed"} == set(live)               # the live finished view's shape, key for key
    for key in ("session", "game_over", "winner", "your_seat", "rung", "rung_name", "rated", "ladder",
                "layout", "board", "context", "moves", "discard", "prompt"):
        assert view[key] == live[key], key
    assert view["reviewed"] is False

    # the rebuilt game log: one line per logged action, labelled as the live game labelled it
    assert len(view["events"]) == len(record["log"])
    state = new_game(record["seed"])
    for entry, ev in zip(record["log"], view["events"]):
        action = tp.action_from_dict(entry["action"])
        assert ev["who"] == ("you" if entry["actor"] == record["human"] else "bot")
        assert ev["label"] == tp._label(action, state, entry["actor"])
        apply_action(state, action)
        if action.type is ActionType.ROLL:
            assert ev["roll"] == state.last_roll
        else:
            assert "roll" not in ev


def test_open_game_says_whether_a_finished_review_is_cached(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_118)
    review = ReviewService(sims=16, dets=2)
    assert review.open_game(sid, None)["reviewed"] is False
    _drain(review, review.start(sid)["review_id"])
    assert review.open_game(sid, None)["reviewed"] is True


def test_open_game_keeps_a_coached_moves_verdict_on_its_log_line(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_119)
    path = tmp_path / f"{sid}.json"
    record = json.loads(path.read_text())
    i = next(i for i, e in enumerate(record["log"]) if e["actor"] == record["human"])
    record["log"][i]["verdict"] = "blunder"
    path.write_text(json.dumps(record))
    events = ReviewService().open_game(sid, None)["events"]
    assert events[i]["verdict"] == "blunder"
    assert not any("verdict" in ev for j, ev in enumerate(events) if j != i)


def test_open_game_is_the_players_own_games_only(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_120)
    path = tmp_path / f"{sid}.json"
    record = json.loads(path.read_text())
    review = ReviewService()
    record["user_id"] = 1
    path.write_text(json.dumps(record))
    assert "error" not in review.open_game(sid, 1)
    assert review.open_game(sid, 2) == {"error": "unknown game"}       # not theirs: not even confirmed to exist
    del record["user_id"]                                              # written before records carried an owner
    path.write_text(json.dumps(record))
    assert "error" not in review.open_game(sid, 1) and "error" not in review.open_game(sid, 2)
    assert review.open_game("g0-nothere", 1) == {"error": "unknown game"}


def test_start_and_poll_require_the_players_own_game(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_124)
    path = tmp_path / f"{sid}.json"
    record = json.loads(path.read_text())
    record["user_id"] = 1
    path.write_text(json.dumps(record))
    review = ReviewService(sims=16, dets=2)

    assert review.start(sid, 2) == {"error": "unknown game"}       # not theirs: not even confirmed to exist
    start = review.start(sid, 1)
    assert "error" not in start
    assert review.poll(start["review_id"], 0, 2) == {"error": "unknown review"}  # can't piggyback another's review
    assert "error" not in review.poll(start["review_id"], 0, 1)
    _drain(review, start["review_id"])

    del record["user_id"]                                          # written before records carried an owner
    path.write_text(json.dumps(record))
    assert "error" not in review.start(sid, 1) and "error" not in review.start(sid, 2)


def test_open_game_reports_a_record_that_no_longer_replays(tmp_path, monkeypatch):
    sid = _play_one_game(tmp_path, monkeypatch, seed=301_121)
    path = tmp_path / f"{sid}.json"
    record = json.loads(path.read_text())
    review = ReviewService()

    stale = dict(record, final_vp=[record["final_vp"][0] + 1, record["final_vp"][1]])   # replays to another result
    path.write_text(json.dumps(stale))
    assert "can no longer be reopened" in review.open_game(sid, None)["error"]

    stale = dict(record, log=record["log"][1:])                                          # an action the engine now rejects
    path.write_text(json.dumps(stale))
    assert "can no longer be reopened" in review.open_game(sid, None)["error"]


# --- HTTP wiring ---


def _games_server(tmp_path, review):
    path, _ = _puzzle_file(tmp_path)
    svc = TrainerService(path, JsonStore(tmp_path / "state.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        svc, play=None, review=review, session_secret="s", secure_cookies=False))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return svc, server, server.server_address[1]


def test_games_routes_list_and_reopen_only_the_players_games(tmp_path, monkeypatch):
    play_dir = tmp_path / "play"
    play_dir.mkdir()
    sid, _ = _play_one_game_view(play_dir, monkeypatch, seed=301_122)
    review = ReviewService(sims=16, dets=2)
    svc, server, port = _games_server(tmp_path, review)
    try:
        assert _request(port, "GET", "/api/games")[0] == 401                          # login required
        _, _, alice = _request(port, "POST", "/api/signup", {"name": "alice", "password": "hunter2pass"})
        _, _, bob = _request(port, "POST", "/api/signup", {"name": "bobby", "password": "hunter2pass"})
        path = play_dir / f"{sid}.json"
        record = json.loads(path.read_text())
        record["user_id"] = svc.store.get_user("alice")["id"]
        path.write_text(json.dumps(record))

        status, out, _ = _request(port, "GET", "/api/games", cookie=alice)
        assert status == 200 and [g["id"] for g in out["games"]] == [sid] and out["review_running"] is None
        assert _request(port, "GET", "/api/games", cookie=bob)[1]["games"] == []

        status, view, _ = _request(port, "GET", f"/api/games/{sid}", cookie=alice)
        assert status == 200 and view["game_over"] and view["session"] == sid
        assert _request(port, "GET", f"/api/games/{sid}", cookie=bob)[0] == 404         # someone else's game
        assert _request(port, "GET", "/api/games/../secret", cookie=alice)[0] == 404    # not a game id
        assert _request(port, "GET", "/api/games/no-such-game", cookie=alice)[0] == 404

        # while a review runs the list says which game it is (so the UI can grey the others)
        release = threading.Event()
        real_run = review._run
        monkeypatch.setattr(review, "_run", lambda rid, rec: (release.wait(timeout=5), real_run(rid, rec)))
        assert _request(port, "POST", "/api/review/start", {"game_id": sid}, cookie=alice)[0] == 200
        assert _request(port, "GET", "/api/games", cookie=alice)[1]["review_running"] == sid
        release.set()
        _drain(review, sid)
        assert _request(port, "GET", "/api/games", cookie=alice)[1]["games"][0]["reviewed"] is True
    finally:
        server.shutdown()


def test_review_start_and_poll_routes_require_login_and_ownership(tmp_path, monkeypatch):
    play_dir = tmp_path / "play"
    play_dir.mkdir()
    sid, _ = _play_one_game_view(play_dir, monkeypatch, seed=301_126)
    review = ReviewService(sims=16, dets=2)
    svc, server, port = _games_server(tmp_path, review)
    try:
        assert _request(port, "POST", "/api/review/start", {"game_id": sid})[0] == 401        # login required
        assert _request(port, "GET", f"/api/review/poll?id={sid}&from=0")[0] == 401

        _, _, alice = _request(port, "POST", "/api/signup", {"name": "alice", "password": "hunter2pass"})
        _, _, bob = _request(port, "POST", "/api/signup", {"name": "bobby", "password": "hunter2pass"})
        path = play_dir / f"{sid}.json"
        record = json.loads(path.read_text())
        record["user_id"] = svc.store.get_user("alice")["id"]
        path.write_text(json.dumps(record))

        assert _request(port, "POST", "/api/review/start", {"game_id": sid}, cookie=bob)[0] == 404  # someone else's
        status, out, _ = _request(port, "POST", "/api/review/start", {"game_id": sid}, cookie=alice)
        assert status == 200
        review_id = out["review_id"]

        assert _request(port, "GET", f"/api/review/poll?id={review_id}&from=0")[0] == 401
        assert _request(port, "GET", f"/api/review/poll?id={review_id}&from=0", cookie=bob)[0] == 404
        assert _request(port, "GET", f"/api/review/poll?id={review_id}&from=0", cookie=alice)[0] == 200
        _drain(review, review_id)
    finally:
        server.shutdown()


def test_games_routes_are_absent_where_review_is_not_available(tmp_path):
    svc, server, port = _games_server(tmp_path, review=None)
    try:
        _, _, cookie = _request(port, "POST", "/api/signup", {"name": "alice", "password": "hunter2pass"})
        status, body, _ = _request(port, "GET", "/api/games", cookie=cookie)
        assert status == 404 and "not available" in body["error"]
    finally:
        server.shutdown()
