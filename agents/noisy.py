"""A strength dial for any agent: play a random plausible move some of the time.

The bot ladder needs opponents SPACED by measurement, but search depth alone
spans only ~250 Elo here: the net's policy is already most of the strength and
the deepest rung bought almost nothing over the cheapest net rung (README,
"Measured: the ladder is far flatter than declared"). So the weaker rungs are
the same bot with a random-move rate: with probability `epsilon` per decision
this wrapper drops the base agent's choice and plays a uniformly random legal
action instead.

- Bank trades are excluded from the random pool (unless nothing else is legal):
  a bot that bank-trades at random reads as churn, not as a mistake — the same
  reasoning as `GreedyRandomAgent`.
- Forced decisions (one legal action) are never touched and consume no
  randomness.
- When the random branch is taken the base agent's search is SKIPPED, so the
  noisier a rung is, the cheaper its moves are.
- The base agent still observes every applied action, so a belief-tracking base
  (the MCTS engine) stays exact after a randomized move.
"""
from __future__ import annotations

import random

from engine import Action, ActionType, GameState, legal_actions

from .base import Agent


class EpsilonAgent(Agent):
    def __init__(self, base: Agent, epsilon: float, seed: int = 0):
        if not 0.0 <= epsilon <= 1.0:
            raise ValueError(f"epsilon must be in [0, 1], got {epsilon}")
        self.base = base
        self.epsilon = epsilon
        self._master_seed = seed
        self._rng = random.Random(seed)
        self.name = f"eps{epsilon:g}({base.name})"

    def begin_game(self, seat: int) -> None:
        self.base.begin_game(seat)
        self._rng = random.Random(self._master_seed + 104729 * seat)

    def observe(self, state: GameState, action: Action) -> None:
        self.base.observe(state, action)

    def select_action(self, state: GameState) -> Action:
        if self.epsilon > 0.0:
            actions = legal_actions(state)
            if len(actions) > 1 and self._rng.random() < self.epsilon:
                pool = [a for a in actions if a.type is not ActionType.TRADE_BANK]
                return self._rng.choice(pool or actions)
        return self.base.select_action(state)
