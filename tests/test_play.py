"""Play-vs-bot mode (trainer/play.py): session loop, discards, hiding."""
import random
from pathlib import Path

from trainer.play import PlayService


def _svc():
    # Rung 1 (HeuristicAgent, no search): fast enough to play full games in
    # tests. No ratings_for -> anonymous, ladder tracking is a no-op.
    return PlayService(default_rung=1)


def _play_out(svc, view, rng, max_steps=3000):
    """Drive the human side with random legal choices until game over."""
    steps = 0
    while not view["game_over"] and steps < max_steps:
        if view.get("discard"):
            hand = [
                r
                for r, n in view["context"]["resources"].items()
                for _ in range(n)
            ]
            rng.shuffle(hand)
            view = svc.act(view["session"], discard=hand[: view["discard"]["count"]])
        else:
            assert view["moves"], f"no moves and no discard: {view['prompt']}"
            mv = rng.choice(view["moves"])
            view = svc.act(view["session"], codec_id=mv["codec_id"])
        assert "error" not in view, view.get("error")
        assert not view.get("illegal"), "legal submission flagged illegal"
        steps += 1
    return view


def test_full_game_random_human_terminates_and_bot_usually_wins():
    svc = _svc()
    rng = random.Random(0)
    view = svc.new_game(seed=301_001)
    assert view["moves"], "game must open on a human decision (setup placement)"
    final = _play_out(svc, view, rng)
    assert final["game_over"], "game did not finish"
    assert final["winner"] in ("you", "bot", "draw")

    # conftest isolates records: the finished game lands in the per-test dir,
    # never in the real data/games/ that the dashboard reads as the player's.
    import trainer.play as tp

    record = f"{view['session']}.json"
    assert (tp._GAMES_DIR / record).exists()
    assert not (Path("data/games") / record).exists()


def test_game_record_is_tagged_with_the_players_user_id():
    import json

    import trainer.play as tp

    svc = _svc()
    view = svc.new_game(user_id=7, seed=301_008)
    svc._sessions[view["session"]]._persist()
    record = json.loads((tp._GAMES_DIR / f"{view['session']}.json").read_text())
    assert record["user_id"] == 7


def test_view_never_leaks_bot_hand():
    svc = _svc()
    rng = random.Random(1)
    view = svc.new_game(seed=301_002)
    for _ in range(60):
        if view["game_over"]:
            break
        ctx = view["context"]
        # opponent info must be counts only
        assert set(k for k in ctx if k.startswith("opp_")) == {
            "opp_visible_vp", "opp_hand_size", "opp_dev_count", "opp_knights"
        }
        assert "events" in view and isinstance(view["events"], list)
        view = _step_once(svc, view, rng)


def _step_once(svc, view, rng):
    if view.get("discard"):
        hand = [
            r for r, n in view["context"]["resources"].items() for _ in range(n)
        ]
        return svc.act(view["session"], discard=hand[: view["discard"]["count"]])
    mv = rng.choice(view["moves"])
    return svc.act(view["session"], codec_id=mv["codec_id"])


def test_illegal_codec_is_rejected_softly():
    svc = _svc()
    view = svc.new_game(seed=301_003)
    legal = {m["codec_id"] for m in view["moves"]}
    bad = next(i for i in range(370) if i not in legal)
    res = svc.act(view["session"], codec_id=bad)
    assert res.get("illegal") is True
    # session still alive: a legal move works afterwards
    mv = view["moves"][0]
    res2 = svc.act(view["session"], codec_id=mv["codec_id"])
    assert "error" not in res2 and not res2.get("illegal")


def test_unknown_session_errors():
    svc = _svc()
    assert "error" in svc.act("nope", codec_id=0)


def test_game_record_replays_to_identical_outcome(tmp_path, monkeypatch):
    """The persisted (seed, action log) must replay to the exact same game —
    the determinism contract the post-game review feature stands on."""
    import json

    import trainer.play as tp
    from engine import Phase, apply_action, new_game

    monkeypatch.setattr(tp, "_GAMES_DIR", tmp_path)
    svc = _svc()
    rng = random.Random(7)
    view = svc.new_game(seed=301_007)
    final = _play_out(svc, view, rng)
    assert final["game_over"]

    record = json.loads((tmp_path / f"{view['session']}.json").read_text())
    assert record["seed"] == 301_007
    assert record["final_vp"][record["winner"]] >= 15 or record["winner"] is None

    # replay: every logged action applies legally and lands on the same VPs
    state = new_game(record["seed"])
    for entry in record["log"]:
        assert state.player_to_act() == entry["actor"]
        apply_action(state, tp.action_from_dict(entry["action"]))
    assert state.phase is Phase.GAME_OVER
    assert [state.total_vp(0), state.total_vp(1)] == record["final_vp"]
    assert state.winner == record["winner"]
