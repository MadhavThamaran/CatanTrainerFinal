"""Play a full seeded game with a build-greedy random agent and print a summary.

Usage: python -m examples.random_game [seed]
"""
from __future__ import annotations

import random
import sys

from engine import ActionType, Phase, apply_action, legal_actions, new_game

# Prefer game-advancing actions so random play converges instead of
# trading/shuffling resources forever.
_PRIORITY = {
    ActionType.BUILD_CITY: 0,
    ActionType.BUILD_SETTLEMENT: 1,
    ActionType.BUILD_ROAD: 2,
    ActionType.BUY_DEV_CARD: 3,
}


def play(seed: int, max_actions: int = 100_000):
    state = new_game(seed)
    agent_rng = random.Random(seed + 1)
    actions_taken = 0
    while state.phase is not Phase.GAME_OVER and actions_taken < max_actions:
        actions = legal_actions(state)
        assert actions, f"no legal actions in phase {state.phase}"
        best_rank = min(_PRIORITY.get(a.type, 4) for a in actions)
        pool = [a for a in actions if _PRIORITY.get(a.type, 4) == best_rank]
        # Mostly greedy, occasionally uniform, never end-turn-only spam.
        if agent_rng.random() < 0.1:
            pool = actions
        apply_action(state, agent_rng.choice(pool))
        actions_taken += 1
    return state, actions_taken


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    state, n = play(seed)
    print(f"seed={seed} actions={n} turns={state.turn_count}")
    if state.winner is not None:
        print(
            f"winner: player {state.winner} "
            f"({state.total_vp(state.winner)} VP vs {state.total_vp(1 - state.winner)})"
        )
    else:
        print("hit action cap without a winner")
