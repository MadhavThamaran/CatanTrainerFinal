"""Full-game smoke test: seeded games under a build-greedy random agent
run to completion (or a generous cap) without ever producing an illegal
state, and every legal-action set is non-empty until the game ends."""
import random

import pytest

from engine import ActionType, Phase, apply_action, legal_actions, new_game

_PRIORITY = {
    ActionType.BUILD_CITY: 0,
    ActionType.BUILD_SETTLEMENT: 1,
    ActionType.BUILD_ROAD: 2,
    ActionType.BUY_DEV_CARD: 3,
}

_MAX_ACTIONS = 20_000


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_full_game_runs_clean(seed):
    state = new_game(seed)
    agent = random.Random(seed + 1)
    for _ in range(_MAX_ACTIONS):
        if state.phase is Phase.GAME_OVER:
            break
        actions = legal_actions(state)
        assert actions, f"stuck: no legal actions, phase={state.phase}"
        best = min(_PRIORITY.get(a.type, 4) for a in actions)
        pool = actions if agent.random() < 0.1 else [
            a for a in actions if _PRIORITY.get(a.type, 4) == best
        ]
        apply_action(state, agent.choice(pool))

        # Invariants that must hold after every single action.
        for p in state.players:
            assert all(n >= 0 for n in p.resources.values())
            assert all(n >= 0 for n in p.dev_cards.values())
            assert p.roads_left >= 0 and p.settlements_left >= 0 and p.cities_left >= 0

    if state.phase is Phase.GAME_OVER:
        assert state.winner in (0, 1)
        assert state.total_vp(state.winner) >= 15
        assert state.total_vp(1 - state.winner) < 15


def test_same_seed_same_game():
    def transcript(seed):
        state = new_game(seed)
        agent = random.Random(99)
        log = []
        for _ in range(500):
            if state.phase is Phase.GAME_OVER:
                break
            action = agent.choice(legal_actions(state))
            apply_action(state, action)
            log.append(repr(action))
        return log, state.to_dict()

    assert transcript(5) == transcript(5)
