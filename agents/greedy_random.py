"""Build-greedy random baseline (PLAN.md Stage 3).

Stronger than uniform random: it always takes the most VP-productive build
available (city > settlement > road > dev card), breaking ties randomly, and
otherwise plays a random non-wasteful action. It never trades speculatively.
This is the "does the heuristic *actually* add value beyond greedy building?"
opponent — beating uniform random is easy; beating this is the real bar.
"""
from __future__ import annotations

import random

from engine import Action, ActionType, GameState, legal_actions

from .base import Agent

# Lower rank = higher priority.
_BUILD_PRIORITY = {
    ActionType.BUILD_CITY: 0,
    ActionType.BUILD_SETTLEMENT: 1,
    ActionType.BUILD_ROAD: 2,
    ActionType.BUY_DEV_CARD: 3,
}


class GreedyRandomAgent(Agent):
    name = "greedy_random"

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def select_action(self, state: GameState) -> Action:
        actions = legal_actions(state)
        builds = [a for a in actions if a.type in _BUILD_PRIORITY]
        if builds:
            best = min(_BUILD_PRIORITY[a.type] for a in builds)
            pool = [a for a in builds if _BUILD_PRIORITY[a.type] == best]
            return self._rng.choice(pool)
        # No build available: play a random action, but drop pointless bank
        # trades so the agent doesn't churn its hand instead of ending the turn.
        non_trade = [a for a in actions if a.type is not ActionType.TRADE_BANK]
        return self._rng.choice(non_trade or actions)
