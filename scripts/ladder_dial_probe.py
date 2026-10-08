"""Dial probe: how does strength move with (net, sims x dets, random-move rate)?

The exploratory run behind the v2 ladder table (LADDER_SPEC §8, README "v2: rungs
as strength dials"). Each config plays `--games` games against a reference bot
(alternating seats) and is rated relative to it on the anchored scale. Reference
"heuristic" is the v1 Settler (1149.4, measured by the 2026-10-06 calibration);
"anchor" is `ladder.ANCHOR`, the raw MCTS 160 x 4 bot that DEFINES 1200. It only
informs the table: the rungs' real ratings come from `scripts/ladder_calibrate.py`.

Config syntax: `<net>:<sims>x<dets>:e<epsilon>`, `net` a checkpoint stem in
`checkpoints/` (e.g. `gen7:16x2:e0.3`); or the bare names `heuristic` / `anchor`.

Config i plays seeds `--seed-offset + 1000*i + game`: the default offset 93,000,000
with up to 10 configs consumes at most 93,009,999 (reserved in CLAUDE.md's seed
ledger). Resume with the SAME offset and config order, or a replayed config gets
fresh seeds. Elo is floored at a 0.5% win rate (230 against the heuristic): a
0-win config, or the lower edge of a 1-win interval, is a floor, not an estimate.

The 2026-10-07 run (80 games, 40 min; data/ladder_dial_probe_report.txt and
data/ladder_dial_probe_results.json):
  uv run python scripts/ladder_dial_probe.py --ref heuristic --games 80 \\
      --configs gen7:64x3:e0 gen7:32x2:e0 gen7:16x2:e0 gen7:8x1:e0 \\
      gen7:32x2:e0.1 gen7:32x2:e0.2 gen7:32x2:e0.3 gen7:32x2:e0.45 \\
      gen7:32x2:e0.65 gen7:32x2:e0.85
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import EpsilonAgent, HeuristicAgent  # noqa: E402
from engine import Phase, apply_action, new_game  # noqa: E402
from trainer import ladder  # noqa: E402

_OUT = "data/ladder_dial_probe_results.json"
MAX_ACTIONS = 6000
REF_ELO = {"heuristic": 1149.4, "anchor": ladder.ANCHOR_ELO}
_NAMED = ("heuristic", "anchor")


def parse_config(cfg: str) -> tuple[str, int, int, float]:
    """`gen7:16x2:e0.3` -> ("gen7", 16, 2, 0.3)."""
    try:
        net_name, search, eps = cfg.split(":")
        sims, dets = (int(x) for x in search.split("x"))
        if not eps.startswith("e"):
            raise ValueError
        return net_name, sims, dets, float(eps[1:])
    except ValueError:
        raise ValueError(f"bad config {cfg!r}: expected <net>:<sims>x<dets>:e<epsilon> "
                         "(e.g. gen7:16x2:e0.3), or heuristic / anchor") from None


def make(cfg: str, seed: int):
    if cfg == "heuristic":
        return HeuristicAgent()
    if cfg == "anchor":
        return ladder.make_bot(ladder.ANCHOR_NUMBER, seed=seed)
    net_name, sims, dets, eps = parse_config(cfg)
    from net.evaluator import get_evaluator
    from search import MCTSEngine

    engine = MCTSEngine(simulations=sims, determinizations=dets, seed=seed,
                        net=get_evaluator(f"checkpoints/{net_name}.pt"))
    return EpsilonAgent(engine, eps, seed=seed + 31) if eps > 0 else engine


def play_one(job: tuple) -> bool:
    """Returns True iff the config under test won."""
    seed, cfg, ref = job
    a_seat = seed % 2
    a, b = make(cfg, seed), make(ref, seed + 999)
    seats = (a, b) if a_seat == 0 else (b, a)
    seats[0].begin_game(0)
    seats[1].begin_game(1)
    state = new_game(seed)
    n = 0
    while state.phase is not Phase.GAME_OVER and n < MAX_ACTIONS:
        action = seats[state.player_to_act()].select_action(state)
        apply_action(state, action)
        seats[0].observe(state, action)
        seats[1].observe(state, action)
        n += 1
    return state.winner == a_seat


def wilson(w: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def to_elo(p: float, ref: float) -> float:
    p = min(max(p, 0.005), 0.995)
    return ref + 400 * math.log10(p / (1 - p))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--configs", nargs="+", required=True)
    ap.add_argument("--ref", default="heuristic", choices=sorted(REF_ELO))
    ap.add_argument("--games", type=int, default=80)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    ap.add_argument("--seed-offset", type=int, default=93_000_000)
    ap.add_argument("--out", default=_OUT)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    for cfg in args.configs:                      # a typo should cost 0 s, not a 40-minute run
        if cfg not in _NAMED:
            parse_config(cfg)

    out = Path(args.out)
    results = json.loads(out.read_text()) if args.resume and out.exists() else {}
    ref_elo = REF_ELO[args.ref]
    print(f"{len(args.configs)} configs x {args.games} games vs {args.ref} (={ref_elo}), "
          f"seeds from {args.seed_offset}", flush=True)
    t0 = time.time()
    for i, cfg in enumerate(args.configs):
        have = results.get(cfg)
        if have and have["games"] == args.games and have["ref"] == args.ref:
            print(f"[{i + 1}/{len(args.configs)}] {cfg}: resumed {have['wins']}/{have['games']}", flush=True)
            continue
        jobs = [(args.seed_offset + 1000 * i + g, cfg, args.ref) for g in range(args.games)]
        t1 = time.time()
        with mp.Pool(args.workers) as pool:
            wins = sum(pool.imap_unordered(play_one, jobs))
        p = wins / args.games
        lo, hi = wilson(wins, args.games)
        results[cfg] = {"wins": wins, "games": args.games, "ref": args.ref,
                        "elo": round(to_elo(p, ref_elo), 1),
                        "elo_lo": round(to_elo(lo, ref_elo), 1), "elo_hi": round(to_elo(hi, ref_elo), 1),
                        "seconds": round(time.time() - t1)}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=1))
        r = results[cfg]
        print(f"[{i + 1}/{len(args.configs)}] {cfg:22s} {wins:3d}/{args.games} = {p:5.1%}  "
              f"Elo {r['elo']:7.1f} ({r['elo_lo']:.0f}..{r['elo_hi']:.0f})  "
              f"{r['seconds']}s  ({time.time() - t0:.0f}s total)", flush=True)
    print("done")


if __name__ == "__main__":
    main()
