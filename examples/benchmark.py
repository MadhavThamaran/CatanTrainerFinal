"""Benchmark the heuristic agent against the random baseline.

Usage: python -m examples.benchmark [games]
"""
from __future__ import annotations

import sys

from agents import GreedyRandomAgent, HeuristicAgent, RandomAgent, evaluate


def _report(label: str, opponent, games: int) -> None:
    report = evaluate(
        agent_a=lambda s: HeuristicAgent(),
        agent_b=opponent,
        games=games,
    )
    print(f"\nheuristic vs {label} over {report.games} games:")
    print(f"  heuristic wins : {report.a_wins}  ({report.a_win_rate:.0%})")
    print(f"  {label} wins   : {report.b_wins}")
    print(f"  draws          : {report.draws}")
    print(f"  heuristic reached 15 VP outright: {report.a_reached_15}")


def main(games: int) -> None:
    _report("random", lambda s: RandomAgent(s), games)
    _report("greedy_random", lambda s: GreedyRandomAgent(s), games)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
