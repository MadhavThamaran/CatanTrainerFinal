"""End-to-end puzzle pipeline (PLAN.md Stage 6 / M6):

    self-play games -> candidate positions -> deep labeling -> admitted
    puzzles with point tables (JSONL)

Both stages run in worker pools. Mining seeds default to 200,000+ so puzzle
boards never overlap net-training (0..) or gate (100,000..) seed ranges.

Usage:
  python -m puzzles.pipeline --games 24 --out data/puzzles_v0.jsonl
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import time
from collections import Counter

from .labeling import label_candidate
from .mining import mine_game


def _label_job(args: tuple):
    candidate, sims, dets, min_gap, net_path = args
    try:
        return label_candidate(
            candidate, sims=sims, dets=dets, min_gap=min_gap, net_path=net_path
        )
    except Exception as exc:  # noqa: BLE001 - one bad candidate must not kill the pool
        return None, f"error:{exc!r}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=24)
    ap.add_argument("--max-midgame-per-game", type=int, default=5)
    ap.add_argument("--sims", type=int, default=960, help="labeling sims per determinization")
    ap.add_argument("--dets", type=int, default=8, help="labeling determinizations")
    ap.add_argument("--min-gap", type=float, default=0.04)
    ap.add_argument("--seed-offset", type=int, default=200_000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    ap.add_argument(
        "--net",
        default=None,
        help="checkpoint: net-guided mining games AND net-guided labeling",
    )
    args = ap.parse_args()

    t0 = time.time()
    jobs = [
        (args.seed_offset + s, args.max_midgame_per_game, args.net)
        for s in range(args.games)
    ]
    candidates: list[dict] = []
    with mp.Pool(args.workers) as pool:
        for i, cands in enumerate(pool.imap_unordered(mine_game, jobs)):
            candidates.extend(cands)
            print(f"[mine {i + 1}/{args.games}] +{len(cands)} candidates "
                  f"(total {len(candidates)}, {time.time() - t0:.0f}s)", flush=True)

    label_jobs = [
        (c, args.sims, args.dets, args.min_gap, args.net) for c in candidates
    ]
    puzzles = []
    reasons: Counter = Counter()
    with mp.Pool(args.workers) as pool:
        for puzzle, reason in pool.imap_unordered(_label_job, label_jobs):
            if puzzle is not None:
                puzzles.append(puzzle)
            reasons[reason] += 1
            done = sum(reasons.values())
            print(f"[label {done}/{len(candidates)}] admitted {len(puzzles)} "
                  f"({time.time() - t0:.0f}s)", flush=True)

    with open(args.out, "w") as f:
        for p in puzzles:
            f.write(p.to_json() + "\n")

    by_phase = Counter(p.phase for p in puzzles)
    by_diff = Counter(p.difficulty for p in puzzles)
    print(f"\nwrote {args.out}: {len(puzzles)} puzzles from "
          f"{len(candidates)} candidates ({args.games} games, {time.time() - t0:.0f}s)")
    print(f"  admission: {dict(reasons)}")
    print(f"  by phase: {dict(by_phase)}")
    print(f"  by difficulty: {dict(by_diff)}")


if __name__ == "__main__":
    main()
