"""Backfill EXPLAIN_SPEC facts + rendered explanation onto an existing
puzzle set, in place. One pass, no relabeling: every field is a cheap
closed-form diff of the stored position (no search) — see
puzzles/explain.py. Rewrites the file atomically; only ADDS/replaces the
`facts`/`explanation` fields, everything else (moves, gap, state, id)
is untouched.

Usage:
  uv run python scripts/annotate_explanations.py data/puzzles_v5.jsonl
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import GameState, legal_actions  # noqa: E402
from net.codec import encode_action  # noqa: E402
from puzzles.explain import move_facts, render  # noqa: E402


def annotate_one(p: dict) -> dict:
    state = GameState.from_dict(p["state"])
    actor = p["actor"]
    by_codec = {
        encode_action(a): a for a in legal_actions(state) if encode_action(a) is not None
    }
    best = by_codec[p["moves"][0]["codec_id"]]
    second = by_codec[p["moves"][1]["codec_id"]]
    facts_best = move_facts(state, best, actor)
    facts_second = move_facts(state, second, actor)
    p["facts"] = {"best": facts_best, "second": facts_second}
    rendered = render(facts_best, facts_second, p["phase"])
    if rendered:
        p["explanation"] = rendered
    return p


def main() -> None:
    path = sys.argv[1]
    lines = [json.loads(line) for line in open(path)]
    t0 = time.time()
    for i, p in enumerate(lines):
        lines[i] = annotate_one(p)
        if (i + 1) % 200 == 0:
            print(f"[{i + 1}/{len(lines)}] ({time.time() - t0:.0f}s)", flush=True)

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for p in lines:
            f.write(json.dumps(p) + "\n")
    os.replace(tmp, path)
    with_facts = sum(1 for p in lines if p.get("facts"))
    print(f"rewrote {path}; {with_facts}/{len(lines)} puzzles now carry facts "
          f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
