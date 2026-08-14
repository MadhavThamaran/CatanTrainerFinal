"""Frozen-puzzle regression check (PLAN Stage 6 validation).

Re-labels a sample of stored puzzles with the current engine and reports
churn: puzzles that are no longer admitted, or whose BEST MOVE changed.
Run after any engine/search/value change — and especially before promoting
a new labeling engine (gen-3+): some churn is expected from a genuinely
stronger engine, but it must be reviewed, not silent.

Usage:
  uv run python scripts/relabel_check.py data/puzzles_v1.jsonl --sample 20
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puzzles import load_puzzles  # noqa: E402
from puzzles.labeling import label_candidate  # noqa: E402


def check_one(args: tuple) -> dict:
    puzzle_json, sims, dets, min_gap, net_path = args
    from puzzles.schema import Puzzle

    old = Puzzle.from_json(puzzle_json)
    cand = {"state": old.state, "actor": old.actor, "phase": old.phase}
    new, reason = label_candidate(
        cand, sims=sims, dets=dets, min_gap=min_gap, net_path=net_path
    )
    if new is None:
        return {"id": old.id, "status": f"no-longer-admitted ({reason})"}
    if new.best_codec_id != old.best_codec_id:
        return {
            "id": old.id,
            "status": "BEST-MOVE-CHANGED",
            "old_best": old.moves[0].action,
            "new_best": new.moves[0].action,
        }
    return {"id": old.id, "status": "stable"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("puzzles")
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    ap.add_argument(
        "--net", default=None, help="checkpoint: relabel with the net-guided engine"
    )
    args = ap.parse_args()

    puzzles = load_puzzles(args.puzzles)
    rng = random.Random(args.seed)
    sample = rng.sample(puzzles, min(args.sample, len(puzzles)))
    jobs = [
        (
            p.to_json(),
            p.label_config["sims"],
            p.label_config["dets"],
            0.04,
            args.net,
        )
        for p in sample
    ]
    results = []
    with mp.Pool(args.workers) as pool:
        for r in pool.imap_unordered(check_one, jobs):
            results.append(r)
            print(f"[{len(results)}/{len(jobs)}] {r['id']}: {r['status']}", flush=True)

    stable = sum(r["status"] == "stable" for r in results)
    print(f"\n{stable}/{len(results)} stable")
    for r in results:
        if r["status"] != "stable":
            print(" ", r)


if __name__ == "__main__":
    main()
