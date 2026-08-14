"""Parallel large-scale evaluation: MCTSEngine vs HeuristicAgent.

Plays N seeded games across worker processes (seats alternate by seed),
records a rich per-game final-state breakdown as JSONL, and prints an
analysis: win rate with a Wilson 95% CI, seat split, and loss anatomy
(how the winner actually won — cities, Longest Road, Largest Army, VP
cards). The per-loss records feed the variance-vs-systematic deep dive.

Usage:
  python -m examples.mcts_eval [--games 100] [--sims 160] [--dets 4]
                               [--engine-seed-offset 0] [--out results.jsonl]
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import time

from agents import HeuristicAgent
from engine import Building, DevCard, Phase, apply_action, legal_actions, new_game
from engine.longest_road import longest_road_length
from search import MCTSEngine

MAX_ACTIONS = 6000


def breakdown(state, p: int) -> dict:
    settlements = sum(
        1 for o, k in state.buildings.values() if o == p and k is Building.SETTLEMENT
    )
    cities = sum(
        1 for o, k in state.buildings.values() if o == p and k is Building.CITY
    )
    return {
        "settlements": settlements,
        "cities": cities,
        "lr": state.longest_road_holder == p,
        "la": state.largest_army_holder == p,
        "knights": state.players[p].knights_played,
        "road_len": longest_road_length(state, p),
        "vp_cards": state.players[p].dev_cards[DevCard.VICTORY_POINT],
        "total_vp": state.total_vp(p),
    }


def play_one(args: tuple) -> dict:
    seed, sims, dets, engine_seed = args
    mcts_seat = seed % 2
    mcts = MCTSEngine(seed=engine_seed, simulations=sims, determinizations=dets)
    heur = HeuristicAgent()
    seats = (mcts, heur) if mcts_seat == 0 else (heur, mcts)
    seats[0].begin_game(0)
    seats[1].begin_game(1)

    state = new_game(seed)
    t0 = time.time()
    n = 0
    while state.phase is not Phase.GAME_OVER and n < MAX_ACTIONS:
        actor = state.player_to_act()
        action = seats[actor].select_action(state)
        apply_action(state, action)
        seats[0].observe(state, action)
        seats[1].observe(state, action)
        n += 1

    return {
        "seed": seed,
        "engine_seed": engine_seed,
        "mcts_seat": mcts_seat,
        "winner": state.winner,
        "mcts_won": state.winner == mcts_seat,
        "turns": state.turn_count,
        "actions": n,
        "secs": round(time.time() - t0, 1),
        "mcts": breakdown(state, mcts_seat),
        "heur": breakdown(state, 1 - mcts_seat),
    }


def wilson_ci(wins: int, games: int, z: float = 1.96) -> tuple[float, float]:
    if games == 0:
        return (0.0, 1.0)
    p = wins / games
    denom = 1 + z * z / games
    center = (p + z * z / (2 * games)) / denom
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denom
    return (center - half, center + half)


def analyze(results: list[dict]) -> None:
    games = len(results)
    wins = sum(r["mcts_won"] for r in results)
    lo, hi = wilson_ci(wins, games)
    print(f"\n=== MCTS vs heuristic: {wins}/{games} = {wins / games:.1%} "
          f"(95% CI {lo:.1%}-{hi:.1%}) ===")

    for seat in (0, 1):
        sub = [r for r in results if r["mcts_seat"] == seat]
        w = sum(r["mcts_won"] for r in sub)
        print(f"  as seat {seat} ({'first' if seat == 0 else 'second'}): "
              f"{w}/{len(sub)} = {w / len(sub):.1%}")

    wins_r = [r for r in results if r["mcts_won"]]
    losses = [r for r in results if not r["mcts_won"]]

    def award_profile(rows: list[dict], side: str) -> str:
        n = len(rows)
        if n == 0:
            return "n/a"
        lr = sum(r[side]["lr"] for r in rows)
        la = sum(r[side]["la"] for r in rows)
        vc = sum(r[side]["vp_cards"] for r in rows) / n
        cities = sum(r[side]["cities"] for r in rows) / n
        return f"LR {lr}/{n}, LA {la}/{n}, avg vp-cards {vc:.1f}, avg cities {cities:.1f}"

    print(f"  when MCTS wins  -> mcts: {award_profile(wins_r, 'mcts')}")
    print(f"                     heur: {award_profile(wins_r, 'heur')}")
    print(f"  when MCTS loses -> heur: {award_profile(losses, 'heur')}")
    print(f"                     mcts: {award_profile(losses, 'mcts')}")

    close = sum(1 for r in losses if r["mcts"]["total_vp"] >= 13)
    blowout = sum(1 for r in losses if r["mcts"]["total_vp"] <= 8)
    print(f"  losses: {len(losses)} total — {close} photo-finish (>=13 VP), "
          f"{blowout} blowouts (<=8 VP)")
    print(f"  loss seeds: {sorted(r['seed'] for r in losses)}")
    secs = sorted(r["secs"] for r in results)
    print(f"  game wall time: median {secs[len(secs) // 2]:.0f}s, "
          f"p90 {secs[int(len(secs) * 0.9)]:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--sims", type=int, default=160)
    ap.add_argument("--dets", type=int, default=4)
    ap.add_argument("--engine-seed-offset", type=int, default=0,
                    help="offset MCTS's internal seed (same boards, different search randomness)")
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    args = ap.parse_args()

    jobs = [
        (seed, args.sims, args.dets, seed + args.engine_seed_offset)
        for seed in range(args.games)
    ]
    results: list[dict] = []
    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        for r in pool.imap_unordered(play_one, jobs):
            results.append(r)
            done = len(results)
            w = sum(x["mcts_won"] for x in results)
            print(f"[{done}/{args.games}] seed {r['seed']}: "
                  f"{'WIN ' if r['mcts_won'] else 'loss'} "
                  f"vps=({r['mcts']['total_vp']},{r['heur']['total_vp']}) "
                  f"running {w}/{done} ({time.time() - t0:.0f}s)", flush=True)

    results.sort(key=lambda r: r["seed"])
    if args.out:
        with open(args.out, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        print(f"wrote {args.out}")
    analyze(results)


if __name__ == "__main__":
    main()
