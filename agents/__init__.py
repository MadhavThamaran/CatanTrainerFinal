"""Agents (PLAN.md Stage 3): heuristic evaluator, random baseline, and the
head-to-head harness used to validate strength and, later, to benchmark the
MCTS engine."""
from .base import Agent
from .greedy_random import GreedyRandomAgent
from .harness import GameResult, MatchReport, evaluate, play_game
from .heuristic import HeuristicAgent, placement_value, production_value
from .random_agent import RandomAgent

__all__ = [
    "Agent",
    "GameResult",
    "GreedyRandomAgent",
    "HeuristicAgent",
    "MatchReport",
    "RandomAgent",
    "evaluate",
    "placement_value",
    "play_game",
    "production_value",
]
