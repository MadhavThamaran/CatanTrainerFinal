"""Measure real ratings for the bot ladder (LADDER_SPEC §5).

Round-robins adjacent rung pairs plus every rung vs rung 3 (the common
anchor), `--games` games/pair via the same seeded-alternating-seats harness
pattern as `examples/net_vs_net.py`/`examples/mcts_eval.py`, then fits Elo
ratings to the observed win rates by repeated mini-Elo updates over the
pairwise results until they stop moving (rung 3 pinned at 1200) — a simple
iterative fit, no numerical dependencies beyond the stdlib.

Writes `data/ladder_calibration.json`; `trainer/ladder.py::rating_for`
prefers a measured rating over the provisional guess once this exists.

This is an ~8-10h run (8 rungs, several ~60-game pairs at up to sims=800) —
run it on an idle machine, once, chunk-resumable via `--resume`.

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


def _pairs() -> list[tuple[int, int]]:
    adjacent = [(n, n + 1) for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG)]
    anchored = [(n, 3) for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG + 1) if n != 3]
    seen: set[tuple[int, int]] = set()
    out = []
    for a, b in adjacent + anchored:
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


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


def _fit(results: dict[tuple[int, int], tuple[int, int]]) -> dict[int, float]:
    """Repeated mini-Elo updates over every pairwise observation until the
    ratings stop moving — rung 3 pinned. `results[(a, b)] = (a_wins, games)`."""
    elo = {r.number: r.provisional_elo for r in ladder.RUNGS}
    pinned = 3
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


def _check_monotonic(elo: dict[int, float]) -> None:
    for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG):
        if elo[n] > elo[n + 1]:
            print(
                f"WARNING: measured rung {n} ({ladder.get_rung(n).name}, "
                f"{elo[n]:.0f}) rates ABOVE rung {n + 1} "
                f"({ladder.get_rung(n + 1).name}, {elo[n + 1]:.0f}) — "
                "reorder the table before shipping."
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--seed-offset", type=int, default=500_000)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    ap.add_argument(
        "--resume", action="store_true",
        help="skip pairs already present in data/ladder_calibration_results.json",
    )
    args = ap.parse_args()

    pairs = _pairs()
    results: dict[tuple[int, int], tuple[int, int]] = {}
    if args.resume and _RESULTS_OUT.exists():
        raw = json.loads(_RESULTS_OUT.read_text())
        results = {tuple(map(int, k.split(","))): tuple(v) for k, v in raw.items()}

    print(f"{len(pairs)} rung pairs, {args.games} games/pair "
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
        _RESULTS_OUT.write_text(
            json.dumps({f"{k[0]},{k[1]}": list(v) for k, v in results.items()})
        )

    elo = _fit(results)
    _check_monotonic(elo)
    _OUT.write_text(json.dumps({"elo": {str(n): round(e, 1) for n, e in elo.items()}}))
    print(f"wrote {_OUT}")
    for n in range(ladder.MIN_RUNG, ladder.MAX_RUNG + 1):
        print(f"  {n}. {ladder.get_rung(n).name}: "
              f"{ladder.get_rung(n).provisional_elo:.0f} -> {elo[n]:.0f}")


if __name__ == "__main__":
    main()
