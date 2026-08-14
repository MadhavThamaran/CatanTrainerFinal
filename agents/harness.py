"""Head-to-head evaluation harness (PLAN.md Stage 3 validation).

Plays full games between two agents and reports win rates. Seating is
alternated across a match so first-player advantage cancels out. If a game
hits the action cap without a 15-VP win, it is decided on total VP (tie =
draw) — this keeps weak/degenerate agents from hanging the harness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from engine import Phase, apply_action, legal_actions, new_game

from .base import Agent

AgentFactory = Callable[[int], Agent]

# A completed 1v1 game is a few hundred actions; the cap is a safety net for
# weak agents that never reach 15 VP.
DEFAULT_MAX_ACTIONS = 6000


@dataclass
class GameResult:
    winner: int | None          # seat index 0/1, or None on a draw
    decided_by: str             # "victory" | "vp_cap" | "draw"
    turns: int
    actions: int
    vps: tuple[int, int]


def play_game(
    seat0: Agent,
    seat1: Agent,
    seed: int,
    max_actions: int = DEFAULT_MAX_ACTIONS,
) -> GameResult:
    state = new_game(seed)
    seats = (seat0, seat1)
    seat0.begin_game(0)
    seat1.begin_game(1)
    n = 0
    while state.phase is not Phase.GAME_OVER and n < max_actions:
        actor = state.player_to_act()
        action = seats[actor].select_action(state)
        apply_action(state, action)
        seat0.observe(state, action)
        seat1.observe(state, action)
        n += 1

    vps = (state.total_vp(0), state.total_vp(1))
    if state.winner is not None:
        return GameResult(state.winner, "victory", state.turn_count, n, vps)
    if vps[0] != vps[1]:
        return GameResult(0 if vps[0] > vps[1] else 1, "vp_cap", state.turn_count, n, vps)
    return GameResult(None, "draw", state.turn_count, n, vps)


@dataclass
class MatchReport:
    games: int
    a_wins: int
    b_wins: int
    draws: int
    a_reached_15: int           # games A won outright (not on the cap)

    @property
    def a_win_rate(self) -> float:
        return self.a_wins / self.games if self.games else 0.0


def evaluate(
    agent_a: AgentFactory,
    agent_b: AgentFactory,
    games: int = 20,
    base_seed: int = 0,
    max_actions: int = DEFAULT_MAX_ACTIONS,
) -> MatchReport:
    """Play `games` games of A vs B, alternating which seat A occupies.
    Factories take a per-game seed so stochastic agents differ across games."""
    a_wins = b_wins = draws = a_15 = 0
    for i in range(games):
        seed = base_seed + i
        a, b = agent_a(seed), agent_b(seed + 10_000)
        a_seat = i % 2  # alternate seating
        seat0, seat1 = (a, b) if a_seat == 0 else (b, a)
        result = play_game(seat0, seat1, seed, max_actions)

        if result.winner is None:
            draws += 1
        elif result.winner == a_seat:
            a_wins += 1
            if result.decided_by == "victory":
                a_15 += 1
        else:
            b_wins += 1
    return MatchReport(games, a_wins, b_wins, draws, a_15)
