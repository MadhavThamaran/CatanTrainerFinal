"""EpsilonAgent: the bot ladder's strength dial — a random plausible move some
of the time, around any base agent."""
from __future__ import annotations

import pytest

from agents import Agent, EpsilonAgent, HeuristicAgent, evaluate
from engine import ActionType, Phase, apply_action, legal_actions, new_game
from helpers import give, make_main_state


class _Stub(Agent):
    """Base agent: first legal action, recording what the wrapper tells it."""

    name = "stub"

    def __init__(self):
        self.calls = 0
        self.began: list[int] = []
        self.observed: list = []

    def select_action(self, state):
        self.calls += 1
        return legal_actions(state)[0]

    def begin_game(self, seat):
        self.began.append(seat)

    def observe(self, state, action):
        self.observed.append(action)


class _MustNotSearch(Agent):
    name = "must-not-search"

    def select_action(self, state):
        raise AssertionError("the base agent was asked to search")


def _free_state():
    """A free decision: the first setup settlement (54 legal spots)."""
    return new_game(11)


def _forced_state():
    """A forced decision: the first MAIN state offers only ROLL."""
    state = new_game(5)
    h = HeuristicAgent()
    h.begin_game(0)
    while state.phase is not Phase.MAIN:
        apply_action(state, h.select_action(state))
    assert len(legal_actions(state)) == 1
    return state


def test_epsilon_zero_is_a_pure_passthrough():
    base = _Stub()
    agent = EpsilonAgent(base, 0.0)
    state = _free_state()
    assert all(agent.select_action(state) == legal_actions(state)[0] for _ in range(50))
    assert base.calls == 50


def test_forced_decisions_are_never_randomized():
    base = _Stub()
    agent = EpsilonAgent(base, 1.0)
    state = _forced_state()
    only = legal_actions(state)[0]
    assert all(agent.select_action(state) == only for _ in range(20))
    assert base.calls == 20                          # delegated, not short-circuited


def test_full_epsilon_plays_random_legal_moves_and_skips_the_search():
    agent = EpsilonAgent(_MustNotSearch(), 1.0, seed=3)
    state = _free_state()
    legal = legal_actions(state)
    picks = [agent.select_action(state) for _ in range(300)]
    assert all(p in legal for p in picks)
    assert len(set(picks)) >= 10                     # genuinely varied


def test_random_moves_never_bank_trade():
    state = make_main_state()
    give(state, state.current_player, wood=5)
    legal = legal_actions(state)
    assert any(a.type is ActionType.TRADE_BANK for a in legal)      # trades ARE on the table
    agent = EpsilonAgent(_MustNotSearch(), 1.0, seed=1)
    picks = [agent.select_action(state) for _ in range(400)]
    assert all(p in legal and p.type is not ActionType.TRADE_BANK for p in picks)


def test_the_random_rate_matches_epsilon():
    base = _Stub()
    agent = EpsilonAgent(base, 0.3, seed=5)
    state = _free_state()
    n = 2000
    for _ in range(n):
        agent.select_action(state)
    # The base is consulted on the ~70% of decisions that stay un-randomized.
    assert 1300 <= base.calls <= 1500, base.calls


def test_same_seed_and_seat_reproduce_and_seats_differ():
    state = _free_state()

    def run(seed, seat):
        agent = EpsilonAgent(_Stub(), 0.5, seed=seed)
        agent.begin_game(seat)
        return [agent.select_action(state) for _ in range(40)]

    assert run(7, 0) == run(7, 0)
    assert run(7, 0) != run(7, 1)
    assert run(7, 0) != run(8, 0)


def test_hooks_reach_the_base_agent():
    base = _Stub()
    agent = EpsilonAgent(base, 0.4)
    state = _free_state()
    agent.begin_game(1)
    action = legal_actions(state)[0]
    agent.observe(state, action)
    assert base.began == [1] and base.observed == [action]


@pytest.mark.parametrize("bad", [-0.01, 1.01])
def test_epsilon_outside_the_unit_interval_is_rejected(bad):
    with pytest.raises(ValueError):
        EpsilonAgent(_Stub(), bad)


def test_wrapping_the_real_engine_only_plays_legal_moves():
    from search import MCTSEngine

    agent = EpsilonAgent(MCTSEngine(simulations=8, determinizations=1, seed=1), 0.5, seed=2)
    agent.begin_game(0)
    other = HeuristicAgent()
    other.begin_game(1)
    state = new_game(3)
    for _ in range(60):
        if state.phase is Phase.GAME_OVER:
            break
        mover = agent if state.player_to_act() == 0 else other
        action = mover.select_action(state)
        assert action in legal_actions(state)
        apply_action(state, action)
        agent.observe(state, action)
        other.observe(state, action)


def test_more_noise_means_weaker_play():
    """The dial must actually move strength: the same heuristic with 0%, 30% and
    100% random moves, each against the plain heuristic (seeds are fixed, so
    this is deterministic, not statistical flakiness)."""
    def rate(eps):
        report = evaluate(
            agent_a=lambda s: EpsilonAgent(HeuristicAgent(), eps, seed=s),
            agent_b=lambda s: HeuristicAgent(),
            games=30,
        )
        return report.a_win_rate

    clean, some, all_random = rate(0.0), rate(0.3), rate(1.0)
    assert all_random < some < clean, (clean, some, all_random)
    assert all_random <= 0.15
