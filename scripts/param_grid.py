"""Search-parameter tuning around net priors (M5 follow-up).

PUCT constants were tuned for z-scored heuristic priors; a trained net's
priors are much sharper, so c_puct / prior temperature deserve a re-check.
Each candidate config plays the DEFAULT config (same checkpoint both
sides, alternating seats, fresh seeds); a config only matters if it beats
the default's engine, not the net.

Usage:
  uv run python scripts/param_grid.py --net checkpoints/gen6.pt --games 60
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Phase, apply_action, new_game  # noqa: E402
from examples.mcts_eval import wilson_ci  # noqa: E402
from search import MCTSEngine  # noqa: E402

# (label, c_puct, prior_temperature); the default is (1.5, 1.0)
CONFIGS = [
    ("c_puct=1.0", 1.0, 1.0),
    ("c_puct=2.25", 2.25, 1.0),
    ("temp=0.7", 1.5, 0.7),
    ("temp=1.4", 1.5, 1.4),
]

_MODEL: dict = {}


def _engine(path: str, seed: int, sims: int, dets: int, c_puct: float, temp: float):
    import torch

    torch.set_num_threads(1)
    from net.evaluator import NetEvaluator
    from net.model import load_checkpoint

    if path not in _MODEL:
        _MODEL[path] = load_checkpoint(path)
    return MCTSEngine(
        simulations=sims,
        determinizations=dets,
        seed=seed,
        c_puct=c_puct,
        net=NetEvaluator(_MODEL[path], prior_temperature=temp),
    )


def play_one(args: tuple) -> bool:
    seed, net, sims, dets, c_puct, temp = args
    a_seat = seed % 2
    a = _engine(net, seed, sims, dets, c_puct, temp)
    b = _engine(net, seed + 999, sims, dets, 1.5, 1.0)
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
    ap.add_argument("--net", required=True)
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--sims", type=int, default=160)
    ap.add_argument("--dets", type=int, default=4)
    ap.add_argument("--seed-offset", type=int, default=196_000)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    args = ap.parse_args()

    for i, (label, c_puct, temp) in enumerate(CONFIGS):
        base = args.seed_offset + i * 1000
        jobs = [
            (base + s, args.net, args.sims, args.dets, c_puct, temp)
            for s in range(args.games)
        ]
        wins = done = 0
        t0 = time.time()
        with mp.Pool(args.workers) as pool:
            for won in pool.imap_unordered(play_one, jobs):
                wins += won
                done += 1
        lo, hi = wilson_ci(wins, done)
        print(
            f"{label}: {wins}/{done} = {wins / done:.1%} vs default "
            f"(95% CI {lo:.1%}-{hi:.1%}, {time.time() - t0:.0f}s)",
            flush=True,
        )


if __name__ == "__main__":
    main()
