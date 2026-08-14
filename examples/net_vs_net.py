"""Checkpoint-vs-checkpoint gating matches (AZ promotion discipline):
challenger and incumbent both play pure net-guided MCTS at equal budgets,
alternating seats on fresh seeds.

Usage:
  python -m examples.net_vs_net --net-a checkpoints/gen3fly.pt \
      --net-b checkpoints/big1.pt --games 60
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import time

from engine import Phase, apply_action, new_game
from examples.mcts_eval import wilson_ci
from search import MCTSEngine

_MODELS: dict = {}


def _engine(path: str, sims: int, dets: int, seed: int) -> MCTSEngine:
    import torch

    torch.set_num_threads(1)
    from net.evaluator import NetEvaluator
    from net.model import load_checkpoint

    if path not in _MODELS:
        _MODELS[path] = load_checkpoint(path)
    return MCTSEngine(
        simulations=sims, determinizations=dets, seed=seed,
        net=NetEvaluator(_MODELS[path]),
    )


def play_one(args: tuple) -> bool:
    seed, sims, dets, net_a, net_b = args
    a_seat = seed % 2
    a = _engine(net_a, sims, dets, seed)
    b = _engine(net_b, sims, dets, seed + 999)
    seats = (a, b) if a_seat == 0 else (b, a)
    seats[0].begin_game(0)
    seats[1].begin_game(1)
    state = new_game(seed)
    n = 0
    while state.phase is not Phase.GAME_OVER and n < 6000:
        action = seats[state.player_to_act()].select_action(state)
        apply_action(state, action)
        seats[0].observe(state, action)
        seats[1].observe(state, action)
        n += 1
    return state.winner == a_seat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--net-a", required=True, help="challenger checkpoint")
    ap.add_argument("--net-b", required=True, help="incumbent checkpoint")
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--sims", type=int, default=160)
    ap.add_argument("--dets", type=int, default=4)
    ap.add_argument("--seed-offset", type=int, default=120_000)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    args = ap.parse_args()

    jobs = [
        (args.seed_offset + s, args.sims, args.dets, args.net_a, args.net_b)
        for s in range(args.games)
    ]
    wins = done = 0
    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        for a_won in pool.imap_unordered(play_one, jobs):
            wins += a_won
            done += 1
            print(f"[{done}/{args.games}] A {wins}/{done} ({time.time() - t0:.0f}s)",
                  flush=True)
    lo, hi = wilson_ci(wins, done)
    print(f"\n{args.net_a} vs {args.net_b}: {wins}/{done} = {wins / done:.1%} "
          f"(95% CI {lo:.1%}-{hi:.1%})")


if __name__ == "__main__":
    main()
