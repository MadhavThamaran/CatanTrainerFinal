"""M4 validation (PLAN.md Stage 4): the evaluator finds forced wins, ranks
every legal move, is deterministic under seed; determinization preserves
public/own info and conserves hidden pools; the card tracker is exact absent
hidden discards."""
import random

import pytest

from engine import (
    Action,
    ActionType,
    DevCard,
    Phase,
    Resource,
    ScriptedDice,
    TOPOLOGY,
    apply_action,
    legal_actions,
)
from helpers import give, make_board, make_main_state, put_city, put_settlement
from search import CardTracker, Determinizer, MCTSEngine

_TINY = dict(simulations=24, determinizations=2, rollout_depth=6, seed=1)


def _near_win_state():
    """Player 0 one city-upgrade away from 15 VP."""
    state = make_main_state()
    for v in (0, 10, 20, 30, 40, 43):
        put_settlement(state, 0, v)          # 6 settlements = 6 VP
    for v in (45, 47, 50, 52):
        put_city(state, 0, v)                # 4 cities = 8 VP  -> 14 visible
    give(state, 0, ore=3, wheat=2)           # exactly a city upgrade
    put_settlement(state, 1, 25)
    put_settlement(state, 1, 35)
    return state


def test_winning_move_ranked_first():
    state = _near_win_state()
    evals = MCTSEngine(**_TINY).evaluate(state)
    assert evals[0].action.type is ActionType.BUILD_CITY
    assert evals[0].q > 0.9  # the search saw the immediate win


def test_evaluate_covers_all_legal_actions_ranked():
    state = make_main_state()
    put_settlement(state, 0, 0)
    give(state, 0, wood=2, brick=2, sheep=1, wheat=1)
    legal = legal_actions(state)
    evals = MCTSEngine(simulations=len(legal) * 3, determinizations=2, seed=2).evaluate(state)
    assert {e.action for e in evals} == set(legal)
    ranks = [(e.visits, e.q) for e in evals]
    assert ranks == sorted(ranks, reverse=True)  # visit-primary ranking


def test_evaluate_deterministic_under_seed():
    state = _near_win_state()
    a = MCTSEngine(**_TINY).evaluate(state)
    b = MCTSEngine(**_TINY).evaluate(state)
    assert a == b


def test_single_legal_action_short_circuits():
    state = make_main_state()
    state.needs_roll = True
    [only] = MCTSEngine(**_TINY).evaluate(state)
    assert only.action.type is ActionType.ROLL


def test_determinization_hides_and_conserves():
    state = make_main_state()
    put_settlement(state, 0, 0)
    put_settlement(state, 1, 20)
    give(state, 0, wood=2, ore=1)
    give(state, 1, brick=3, wheat=2)                     # opp hand size 5
    state.players[1].dev_cards[DevCard.KNIGHT] = 2
    state.players[1].dev_cards[DevCard.VICTORY_POINT] = 1
    state.players[1].dev_bought_this_turn[DevCard.KNIGHT] = 1

    true_pool = sorted(
        [c.value for c in state.dev_deck]
        + [c.value for c, n in state.players[1].dev_cards.items() for _ in range(n)]
    )
    det = Determinizer().sample(state, viewer=0, rng=random.Random(9))

    # Viewer's own state exact; public state exact.
    assert det.players[0].resources == state.players[0].resources
    assert det.players[0].dev_cards == state.players[0].dev_cards
    assert det.buildings == state.buildings
    assert det.robber_hex == state.robber_hex
    assert det.board is state.board
    # Opponent: sizes/counts preserved, composition resampled from the pool.
    assert det.players[1].hand_size() == 5
    assert sum(det.players[1].dev_cards.values()) == 3
    assert sum(det.players[1].dev_bought_this_turn.values()) == 1
    det_pool = sorted(
        [c.value for c in det.dev_deck]
        + [c.value for c, n in det.players[1].dev_cards.items() for _ in range(n)]
    )
    assert det_pool == true_pool                          # nothing invented or lost
    assert len(det.dev_deck) == len(state.dev_deck)


def test_tracker_exact_without_hidden_events():
    board = make_board()
    # A hex whose number appears once -> unambiguous production.
    hexid, number = next(
        (h, n)
        for h, n in enumerate(board.numbers)
        if n is not None and board.numbers.count(n) == 1
    )
    resource = board.terrain[hexid].resource
    state = make_main_state(board=board, dice=ScriptedDice([number, number, number, number]))
    put_settlement(state, 1, TOPOLOGY.hex_vertices[hexid][0])
    state.needs_roll = True

    tracker = CardTracker(viewer=0)

    def step(action):
        apply_action(state, action)
        tracker.observe(state, action)

    step(Action(ActionType.ROLL, 0))                       # opp +1 resource
    step(Action(ActionType.END_TURN, 0))
    step(Action(ActionType.ROLL, 1))                       # opp +1
    step(Action(ActionType.END_TURN, 1))
    step(Action(ActionType.ROLL, 0))                       # opp +1
    step(Action(ActionType.END_TURN, 0))
    step(Action(ActionType.ROLL, 1))                       # opp +1 -> 4 total
    give(state, 1, **{resource.value: 0})                  # no-op, clarity
    step(Action(ActionType.TRADE_BANK, 1, give=resource, get=Resource.WHEAT))

    assert tracker.exact
    assert tracker.known == state.players[1].resources
    # And sampling in exact mode reproduces the hand exactly.
    sampled = tracker.sample_hand(state, random.Random(0))
    assert sampled == state.players[1].resources


def test_mcts_agent_plays_only_legal_actions():
    from engine import new_game

    engine = MCTSEngine(simulations=12, determinizations=1, rollout_depth=4, seed=3)
    engine.begin_game(0)
    state = new_game(11)
    for _ in range(60):
        if state.phase is Phase.GAME_OVER:
            break
        actor = state.player_to_act()
        legal = legal_actions(state)
        action = engine.select_action(state) if actor == 0 else legal[0]
        assert action in legal
        apply_action(state, action)
        engine.observe(state, action)


@pytest.mark.slow
def test_mcts_beats_greedy_random_baseline():
    from agents import GreedyRandomAgent, evaluate

    report = evaluate(
        agent_a=lambda s: MCTSEngine(
            simulations=64, determinizations=2, rollout_depth=0, seed=s
        ),
        agent_b=lambda s: GreedyRandomAgent(s),
        games=4,
    )
    assert report.a_wins >= 3, report
