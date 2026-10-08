"""Measure real ratings for the bot ladder (LADDER_SPEC §5).

Plays every adjacent rung pair plus every rung against the ANCHOR bot
(`ladder.ANCHOR`: raw MCTS 160x4, defined as 1200, the scale the v1 calibration
set), `--games` games/pair via the same seeded-alternating-seats harness pattern
as `examples/net_vs_net.py`/`examples/mcts_eval.py`, then fits Elo ratings to the
observed win rates by repeated mini-Elo updates over the pairwise results until
they stop moving (anchor pinned) — a simple iterative fit, no numerical
dependencies beyond the stdlib.

The rungs are monotone in strength BY CONSTRUCTION (they are dials: search depth
and a random-move rate), so a measured inversion between neighbors is sampling
noise, not information. The fit is therefore followed by pool-adjacent-violators
smoothing, and any pooled rungs are reported.

Writes `data/ladder_calibration.json` (`elo`, a per-rung `config` signature and
the `smoothed` groups); `trainer/ladder.py::rating_for` prefers a measured rating
over the declared target, but only while the rung's signature still matches.
Pair results are saved per pair in `data/ladder_calibration_results.json` WITH the
signatures they were played under, so `--resume` only reuses pairs whose bots are
unchanged — a re-specced rung replays its pairs.

Cost is dominated by the pairs that involve the deepest-searching rungs (the v1
run, with 800-sims rungs, took 14 h on 14 workers); weaker rungs skip their
search whenever they play a random move, so they are cheap.

Pair i plays seeds `--seed-offset + 1000*i + game`: the default offset 92,000,000
with 15 pairs consumes up to 92,014,999 (reserved in CLAUDE.md's seed ledger).
Resume with the SAME offset or the replayed pair gets fresh seeds.

Usage:
  uv run python scripts/ladder_calibrate.py --games 60 --resume
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Phase, apply_action, new_game  # noqa: E402
from trainer import ladder  # noqa: E402

_OUT = Path("data/ladder_calibration.json")
_RESULTS_OUT = Path("data/ladder_calibration_results.json")
MAX_ACTIONS = 6000
FIT_K = 8.0
FIT_ITERS = 20_000
RESULTS_VERSION = 2


def _pairs() -> list[tuple[int, int]]:
    """Adjacent rungs, then every rung against the anchor. `(a, b)`: wins are
    counted for `a`."""
    adjacent = [(n, n + 1) for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG)]
    anchored = [(n, ladder.ANCHOR_NUMBER) for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG + 1)]
    return adjacent + anchored


def play_one(args: tuple) -> bool:
    """Returns True iff rung `a` won."""
    seed, rung_a, rung_b = args
    a_seat = seed % 2
    a = ladder.make_bot(rung_a, seed=seed)
    b = ladder.make_bot(rung_b, seed=seed + 999)
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


def _fit(results: dict[tuple[int, int], tuple[float, int]]) -> dict[int, float]:
    """Repeated mini-Elo updates over every pairwise observation until the
    ratings stop moving — the anchor pinned. `results[(a, b)] = (a_wins, games)`.
    Returns every bot's rating, the anchor included."""
    elo = {r.number: r.provisional_elo for r in ladder.RUNGS}
    elo[ladder.ANCHOR_NUMBER] = ladder.ANCHOR_ELO
    pinned = ladder.ANCHOR_NUMBER
    for _ in range(FIT_ITERS):
        moved = 0.0
        for (a, b), (a_wins, games) in results.items():
            if games == 0:
                continue
            observed = a_wins / games
            expected = 1.0 / (1.0 + 10 ** ((elo[b] - elo[a]) / 400.0))
            delta = FIT_K * (observed - expected) * (games / 60.0)
            if a != pinned:
                elo[a] += delta
                moved += abs(delta)
            if b != pinned:
                elo[b] -= delta
                moved += abs(delta)
        if moved < 1e-4:
            break
    return elo


def _monotone(elo: dict[int, float]) -> tuple[dict[int, float], list[list[int]]]:
    """Pool adjacent violators over rungs 1..N: the nearest non-decreasing
    ratings. Returns them and the groups of rungs that had to be pooled."""
    blocks: list[list] = []                    # [sum, count, [rungs]]
    for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG + 1):
        blocks.append([elo[n], 1, [n]])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c, rungs = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
            blocks[-1][2] += rungs
    out: dict[int, float] = {}
    pooled: list[list[int]] = []
    for s, c, rungs in blocks:
        for n in rungs:
            out[n] = s / c
        if len(rungs) > 1:
            pooled.append(rungs)
    return out, pooled


def _signatures() -> dict[int, str]:
    return {n: ladder.rung_signature(n) for n in range(ladder.ANCHOR_NUMBER, ladder.MAX_RUNG + 1)}


def _load_results(path: Path, now: dict[int, str]) -> dict[tuple[int, int], tuple[int, int]]:
    """Saved pairs that were played by the bots the table defines TODAY."""
    raw = json.loads(path.read_text())
    if "pairs" not in raw:
        print(f"{path}: older results format (no config signatures) — not reused")
        return {}
    kept: dict[tuple[int, int], tuple[int, int]] = {}
    for key, rec in raw["pairs"].items():
        a, b = (int(x) for x in key.split(","))
        if rec.get("sig") == [now[a], now[b]]:
            kept[(a, b)] = (rec["wins"], rec["games"])
        else:
            print(f"pair {a} vs {b}: played by a different bot than the table now defines — replaying")
    return kept


def _save_results(results: dict[tuple[int, int], tuple[int, int]], now: dict[int, str]) -> None:
    _RESULTS_OUT.write_text(json.dumps({
        "version": RESULTS_VERSION,
        "pairs": {
            f"{a},{b}": {"wins": w, "games": g, "sig": [now[a], now[b]]}
            for (a, b), (w, g) in results.items()
        },
    }))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--seed-offset", type=int, default=92_000_000)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    ap.add_argument(
        "--resume", action="store_true",
        help="skip pairs already present (and still valid) in data/ladder_calibration_results.json",
    )
    args = ap.parse_args()

    pairs = _pairs()
    now = _signatures()
    results: dict[tuple[int, int], tuple[int, int]] = {}
    if args.resume and _RESULTS_OUT.exists():
        results = _load_results(_RESULTS_OUT, now)

    print(f"{len(pairs)} pairs, {args.games} games/pair "
          f"({len(pairs) * args.games} games total)")
    t0 = time.time()
    for i, (a, b) in enumerate(pairs):
        if (a, b) in results:
            print(f"[{i + 1}/{len(pairs)}] {a} vs {b}: resumed "
                  f"({results[(a, b)][0]}/{results[(a, b)][1]})")
            continue
        jobs = [
            (args.seed_offset + 1000 * i + s, a, b) for s in range(args.games)
        ]
        with mp.Pool(args.workers) as pool:
            wins = sum(pool.imap_unordered(play_one, jobs))
        results[(a, b)] = (wins, args.games)
        print(f"[{i + 1}/{len(pairs)}] rung {a} ({ladder.get_rung(a).name}) vs "
              f"rung {b} ({ladder.get_rung(b).name}): {wins}/{args.games} "
              f"({time.time() - t0:.0f}s elapsed)", flush=True)
        _save_results(results, now)

    fitted = _fit(results)
    elo, pooled = _monotone({n: fitted[n] for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG + 1)})
    for group in pooled:
        print(f"NOTE: rungs {group} fitted out of order — pooled (the rungs are monotone "
              "by construction, so this is sampling noise).")
    _OUT.write_text(json.dumps({
        "elo": {str(n): round(e, 1) for n, e in elo.items()},
        "config": {str(n): now[n] for n in elo},
        "smoothed": pooled,
    }))
    print(f"wrote {_OUT}")
    for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG + 1):
        print(f"  {n}. {ladder.get_rung(n).name}: "
              f"{ladder.get_rung(n).provisional_elo:.0f} -> {elo[n]:.0f}"
              + (f"  (fitted {fitted[n]:.0f})" if abs(fitted[n] - elo[n]) > 0.5 else ""))


if __name__ == "__main__":
    main()
