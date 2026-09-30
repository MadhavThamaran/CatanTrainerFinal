"""Live pre-move coaching in play-vs-bot (COACH_SPEC): a flagged move
bounces with the position UNCHANGED, "play it anyway" applies it with a
verdict badge, repeated checks at one decision hit the eval cache, the
toggle works mid-game, and discards go through the same gate."""
from __future__ import annotations

from engine import Phase, Resource, legal_actions
from search.engine import MoveEval

from trainer.play import PlayService


def _svc():
    # Rung 1 (HeuristicAgent, no search): fast and deterministic for tests
    # that don't monkeypatch evaluate() outright. The coach engine is
    # unaffected by rung choice (COACH_SPEC: fixed reference net).
    return PlayService(default_rung=1)


def _force_evals(s, blunder_codec_id, regret=0.3):
    """Replace the coach's ranking of the CURRENT decision with a
    controlled one: the move with this codec id trails the best by exactly
    `regret`; every other legal move ties for best."""
    from net.codec import encode_action

    actions = legal_actions(s.state)
    evals = [
        MoveEval(a, 0.9 - regret if encode_action(a) == blunder_codec_id else 0.9, 10)
        for a in actions
    ]
    evals.sort(key=lambda e: e.q, reverse=True)
    s.coach_engine.evaluate = lambda state, viewer=None: evals


def _new(svc, seed, coach=True):
    view = svc.new_game(seed=seed, coach=coach)
    return svc._sessions[view["session"]], view


def test_flagged_move_bounces_with_state_and_log_unchanged():
    svc = _svc()
    s, view = _new(svc, 301_101)
    blunder_codec = view["moves"][0]["codec_id"]
    _force_evals(s, blunder_codec, regret=0.3)
    n_before, log_before = s.n_actions, list(s.log)

    res = svc.act(view["session"], codec_id=blunder_codec)

    assert "coach" in res
    assert res["coach"]["severity"] == "blunder"   # regret 0.3 >= blunders-only (0.15)
    assert abs(res["coach"]["regret"] - 0.3) < 1e-6
    assert "hint_category" in res["coach"]
    assert s.n_actions == n_before
    assert s.log == log_before
    assert not s._persisted


def test_confirm_applies_with_verdict_and_coached_flag():
    svc = _svc()
    s, view = _new(svc, 301_102)
    blunder_codec = view["moves"][0]["codec_id"]
    _force_evals(s, blunder_codec, regret=0.3)

    bounced = svc.act(view["session"], codec_id=blunder_codec)
    assert "coach" in bounced

    applied = svc.act(view["session"], codec_id=blunder_codec, confirm=True)
    assert "coach" not in applied
    assert not applied.get("illegal")
    ev = applied["events"][0]
    assert ev["coached"] == "confirmed"
    assert ev["verdict"] == "blunder"   # points_for_regret(0.3) == -25


def test_repeated_check_at_one_decision_evaluates_once():
    svc = _svc()
    s, view = _new(svc, 301_103)
    blunder_codec = view["moves"][0]["codec_id"]
    _force_evals(s, blunder_codec, regret=0.3)

    calls = []
    forced = s.coach_engine.evaluate
    s.coach_engine.evaluate = lambda state, viewer=None: (calls.append(1), forced(state, viewer))[1]

    svc.act(view["session"], codec_id=blunder_codec)          # 1st check: bounces
    svc.act(view["session"], codec_id=blunder_codec)          # re-pick same move: cache hit

    assert len(calls) == 1


def test_toggle_mid_game_does_not_apply_a_move():
    svc = _svc()
    s, view = _new(svc, 301_104, coach=False)
    sid = view["session"]
    assert s.coach_on is False

    res = svc.act(sid, coach_set=True)
    assert s.coach_on is True
    assert res["session"] == sid
    assert not res["events"]

    svc.act(sid, coach_set=False)
    assert s.coach_on is False


def test_badges_only_appear_when_coach_on():
    svc = _svc()
    s_off, view_off = _new(svc, 301_105, coach=False)
    res_off = svc.act(view_off["session"], codec_id=view_off["moves"][0]["codec_id"])
    assert all("verdict" not in ev for ev in res_off["events"])

    s_on, view_on = _new(svc, 301_106, coach=True)
    res_on = svc.act(view_on["session"], codec_id=view_on["moves"][0]["codec_id"])
    assert any("verdict" in ev for ev in res_on["events"])


def test_discard_interjection_round_trip():
    svc = _svc()
    s, _ = _new(svc, 301_107)
    # Fast-forward past setup into a synthetic MAIN-phase discard: exercise
    # coach gating on `submit()` directly (not `svc.act`, whose `advance()`
    # would otherwise cascade this ad-hoc state through a needs_roll turn).
    s.state.phase = Phase.MAIN
    s.state.pending_discards = [s.human]
    for r, n in ((Resource.WOOD, 4), (Resource.BRICK, 4), (Resource.SHEEP, 4)):
        s.state.players[s.human].resources[r] = n

    actions = legal_actions(s.state)
    assert len(actions) > 1, "fixture needs a real discard choice"
    blunder = actions[0]
    evals = sorted(
        (MoveEval(a, 0.6 if a == blunder else 0.9, 10) for a in actions),
        key=lambda e: e.q, reverse=True,
    )
    s.coach_engine.evaluate = lambda state, viewer=None: evals
    discard = [r.value for r in blunder.resources]

    bounced = s.submit(discard=discard)
    assert "coach" in bounced
    assert s.state.pending_discards == [s.human]

    applied = s.submit(discard=discard, confirm=True)
    assert applied == {"applied": True}
    assert s.state.pending_discards == []
    ev = s.events[-1]
    assert ev["coached"] == "confirmed"
    assert ev["verdict"] == "blunder"
