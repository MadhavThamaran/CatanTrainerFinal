"""Agent interface (PLAN.md Stage 3).

An agent is a policy: given a full GameState (the perfect/simulation view —
these agents are used as benchmark opponents and MCTS rollout policies, so
they are allowed to see latent state), return one legal Action.
"""
from __future__ import annotations

from engine import Action, GameState


class Agent:
    name: str = "agent"

    def select_action(self, state: GameState) -> Action:
        raise NotImplementedError

    def begin_game(self, seat: int) -> None:
        """Called by the harness once per game with this agent's seat.
        Stateful agents (e.g. the MCTS engine's belief tracker) reset here."""

    def observe(self, state: GameState, action: Action) -> None:
        """Called by the harness after every applied action (both players'),
        with the post-action state. Lets agents maintain beliefs across a
        game. Default: no-op."""

    def __repr__(self) -> str:
        return f"<{self.name}>"
