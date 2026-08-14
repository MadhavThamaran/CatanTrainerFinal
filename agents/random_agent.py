"""Uniform-random baseline (PLAN.md Stage 3): the weak opponent the
heuristic agent must dominate."""
from __future__ import annotations

import random

from engine import Action, GameState, legal_actions

from .base import Agent


class RandomAgent(Agent):
    name = "random"

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def select_action(self, state: GameState) -> Action:
        return self._rng.choice(legal_actions(state))
