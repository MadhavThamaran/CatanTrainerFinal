"""M6 validation: state round-trip, scoring table, admission logic, a
deterministic near-win puzzle admission, and JSONL schema round-trip."""
import random

import pytest

from engine import (
    ActionType,
    GameState,
    Phase,
    apply_action,
    legal_actions,
    new_game,
)
from helpers import give, make_main_state, put_city, put_settlement
from puzzles import (
    Puzzle,
    label_candidate,
    load_puzzles,
    points_for_regret,
    score_move,
)
from puzzles.scoring import BEST_POINTS, BLUNDER_POINTS, TIE_EPSILON


def _advance(state, plies, seed=0):
    rng = random.Random(seed)
    for _ in range(plies):
        if state.phase is Phase.GAME_OVER:
            break
        apply_action(state, rng.choice(legal_actions(state)))
    return state


# --- state round trip (closes the M1 from_dict TODO) ---

def test_state_round_trip_preserves_everything_public():
    state = _advance(new_game(42), 150)
    d = state.to_dict()
    restored = GameState.from_dict(d, seed=7)
    assert restored.to_dict() == d           # exact serialization fixed point
    # And the game is playable from here with identical legal actions.
    assert legal_actions(restored) == legal_actions(state)
    apply_action(restored, legal_actions(restored)[0])


def test_state_round_trip_mid_setup():
    state = _advance(new_game(3), 3)         # inside the snake draft
    restored = GameState.from_dict(state.to_dict())
    assert legal_actions(restored) == legal_actions(state)


# --- scoring table ---

def test_points_table_matches_plan():
    assert points_for_regret(0.0) == BEST_POINTS
    assert points_for_regret(TIE_EPSILON) == BEST_POINTS   # tie
    assert points_for_regret(0.015) == 75
    assert points_for_regret(0.04) == 40
    assert points_for_regret(0.08) == 10
    assert points_for_regret(0.13) == 0
    assert points_for_regret(0.30) == BLUNDER_POINTS
    # monotone non-increasing
    pts = [points_for_regret(d / 100) for d in range(0, 40)]
    assert pts == sorted(pts, reverse=True)


# --- admission on a deterministic sharp position ---

def _near_win_candidate():
    """A RACE with exactly one winning move: p0 is at 14 (one city upgrade
    from 15) — but p1 is ALSO at 14 with a winning upgrade in hand, so
    passing the turn loses. The Catan 'mate in one'."""
    from engine import DevCard

    state = make_main_state()
    for v in (0, 10, 20, 30, 40, 43):
        put_city(state, 0, v)              # 12 visible VP
    put_settlement(state, 0, 25)           # +1 -> 13 visible
    state.players[0].dev_cards[DevCard.VICTORY_POINT] = 1  # 14 total
    give(state, 0, ore=3, wheat=2, wood=4)  # the upgrade + trade chaff (fan >= 3)
    for v in (2, 5, 8, 14, 17, 47):
        put_city(state, 1, v)              # p1: 12 visible
    put_settlement(state, 1, 35)
    put_settlement(state, 1, 52)           # p1: 14 visible
    give(state, 1, ore=3, wheat=2)         # p1 wins next turn if allowed
    return {"state": state.to_dict(), "actor": 0, "phase": "midgame"}


def test_near_win_position_is_admitted_and_scored():
    puzzle, reason = label_candidate(
        _near_win_candidate(), sims=96, dets=2, min_gap=0.04, seeds=(1, 2)
    )
    assert reason == "admitted"
    assert puzzle.phase == "midgame"
    best = puzzle.moves[0]
    assert "BUILD_CITY" in best.action
    assert best.points == BEST_POINTS
    assert best.codec_id == puzzle.best_codec_id
    # END_TURN forfeits the immediate win -> scored below full credit.
    end_turn = next(m for m in puzzle.moves if "END_TURN" in m.action)
    assert end_turn.points < BEST_POINTS
    assert score_move(puzzle, best.codec_id) == BEST_POINTS
    with pytest.raises(KeyError):
        score_move(puzzle, -1)


def test_decided_positions_are_rejected():
    # Same position but give player 0 an overwhelming 16-VP total via VP
    # cards: every line wins -> "decided" or no clear best; never admitted.
    cand = _near_win_candidate()
    cand["state"]["players"][0]["dev_cards"]["victory_point"] = 5
    puzzle, reason = label_candidate(cand, sims=48, dets=2, min_gap=0.04, seeds=(1, 2))
    assert puzzle is None
    assert reason in ("decided", "no-clear-best", "unstable-best")


def test_puzzle_jsonl_round_trip(tmp_path):
    puzzle, reason = label_candidate(
        _near_win_candidate(), sims=96, dets=2, min_gap=0.04, seeds=(1, 2)
    )
    assert reason == "admitted"
    path = tmp_path / "p.jsonl"
    path.write_text(puzzle.to_json() + "\n")
    [loaded] = load_puzzles(str(path))
    assert loaded == puzzle
    # The stored state must reconstruct into the same decision point.
    restored = GameState.from_dict(loaded.state)
    assert restored.player_to_act() == loaded.actor
    assert len(legal_actions(restored)) == len(loaded.moves)


def test_mining_produces_tagged_candidates():
    from puzzles.mining import mine_game

    cands = mine_game((200_500, 3))
    assert cands, "a full game must yield candidates"
    phases = {c["phase"] for c in cands}
    assert "placement" in phases                     # 4 draft picks always exist
    valid = ("placement", "robber", "endgame", "devcard", "trade", "midgame")
    for c in cands:
        assert c["phase"] in valid
        restored = GameState.from_dict(c["state"])
        assert restored.player_to_act() == c["actor"]
        assert len(legal_actions(restored)) > 1
    # every 15-VP game passes through the endgame band, and the tag must
    # take precedence over devcard/trade in that band
    assert "endgame" in phases
    from engine.types import VP_TO_WIN

    from puzzles.mining import ENDGAME_VP

    assert ENDGAME_VP < VP_TO_WIN
    for c in cands:
        s = GameState.from_dict(c["state"])
        at_race = max(s.total_vp(0), s.total_vp(1)) >= ENDGAME_VP
        if c["phase"] in ("devcard", "trade", "midgame"):
            assert not at_race, f"{c['phase']} candidate inside the endgame band"