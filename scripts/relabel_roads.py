"""Recompute placement puzzles' setup-road followup tables in place.

The original road stage was labeled at half-sims/2-seeds and its Q-noise
exceeded TIE_EPSILON (median 2-of-3 roads scored 100 in v3). This re-runs
`_label_setup_road` at the improved full budget for every placement puzzle
in a JSONL set and rewrites the file atomically. Settlement tables are
untouched.

Usage:
  uv run python scripts/relabel_roads.py data/puzzles_v3.jsonl \
      --net checkpoints/gen6.pt --workers 2
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puzzles.labeling import _label_setup_road  # noqa: E402


def redo_one(args: tuple) -> tuple[str, dict | None]:
    puzzle_json, net_path = args
    from engine import Action, ActionType
    from puzzles.schema import Puzzle

    p = Puzzle.from_json(puzzle_json)
    cand = {"state": p.state, "actor": p.actor, "phase": p.phase}
    cfg = p.label_config
    # Placement best moves are SETUP_PLACE_SETTLEMENT; codec id == vertex id.
    assert 0 <= p.best_codec_id < 54, p.best_codec_id
    best = Action(ActionType.SETUP_PLACE_SETTLEMENT, p.actor, vertex=p.best_codec_id)
    followup = _label_setup_road(
        cand,
        best,
        sims=cfg["sims"],
        dets=cfg["dets"],
        seeds=tuple(cfg["seeds"]),
        net_path=net_path,
    )
    return p.id, followup


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("puzzles")
    ap.add_argument("--net", default=None)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    lines = [json.loads(l) for l in open(args.puzzles)]
    jobs = [
        (json.dumps(p), args.net)
        for p in lines
        if p["phase"] == "placement"
    ]
    print(f"{len(jobs)} placement puzzles to re-road of {len(lines)} total")
    new_fu: dict = {}
    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        for i, (pid, fu) in enumerate(pool.imap_unordered(redo_one, jobs)):
            new_fu[pid] = fu
            print(f"[{i + 1}/{len(jobs)}] {pid} "
                  f"({'ok' if fu else 'no-followup'}, {time.time() - t0:.0f}s)",
                  flush=True)

    for p in lines:
        if p["id"] in new_fu:
            p["followup"] = new_fu[p["id"]]
    tmp = args.puzzles + ".tmp"
    with open(tmp, "w") as f:
        for p in lines:
            f.write(json.dumps(p) + "\n")
    os.replace(tmp, args.puzzles)
    flat = sum(
        1
        for p in lines
        if p["phase"] == "placement" and p.get("followup")
        and all(m["points"] == 100 for m in p["followup"]["moves"])
    )
    print(f"rewrote {args.puzzles}; fully-flat road tables now: {flat}")


if __name__ == "__main__":
    main()
