"""M4 strength benchmark: MCTSEngine vs the Stage 3 HeuristicAgent.

PLAN.md M4 exit criterion: >= 70% win rate. Prints per-game results as they
finish (this run takes minutes, not seconds).

Usage: python -m examples.strength_benchmark [games] [simulations] [determinizations] [rollout_depth]
"""
from __future__ import annotations

import sys
import time

from agents import HeuristicAgent, play_game
from search import MCTSEngine


def main(games: int, sims: int, dets: int, depth: int) -> None:
    wins = 0
    t0 = time.time()
    for i in range(games):
        mcts_seat = i % 2
        mcts = MCTSEngine(
            simulations=sims, determinizations=dets, rollout_depth=depth, seed=i
        )
        heur = HeuristicAgent()
        seat0, seat1 = (mcts, heur) if mcts_seat == 0 else (heur, mcts)
        r = play_game(seat0, seat1, seed=i)
        won = r.winner == mcts_seat
        wins += won
        print(
            f"game {i:2d}: mcts_seat={mcts_seat} {'WIN ' if won else 'loss'} "
            f"({r.decided_by}, vps={r.vps}, turns={r.turns}) "
            f"[{wins}/{i + 1}, {time.time() - t0:.0f}s]",
            flush=True,
        )
    print(f"\nMCTS(s={sims},k={dets},d={depth}) vs heuristic: "
          f"{wins}/{games} = {wins / games:.0%}  (target >= 70%)")


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]]
    games = args[0] if len(args) > 0 else 10
    sims = args[1] if len(args) > 1 else 80
    dets = args[2] if len(args) > 2 else 4
    depth = args[3] if len(args) > 3 else 16
    main(games, sims, dets, depth)
