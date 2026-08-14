"""M5 strength evaluation: net-guided MCTS vs raw (heuristic-prior) MCTS at
EQUAL simulation budgets — the PLAN.md M5 exit criterion — or vs the Stage 3
heuristic agent.

Usage:
  python -m examples.net_eval --net checkpoints/gen1.pt --games 60
  python -m examples.net_eval --net ... --opponent heuristic
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import time

from agents import HeuristicAgent
from engine import Phase, apply_action, new_game
from examples.mcts_eval import breakdown, wilson_ci
from search import MCTSEngine

MAX_ACTIONS = 6000


def play_one(args: tuple) -> dict:
    seed, sims, dets, net_path, opponent, mix, temp, vblend = args
    from net.evaluator import NetEvaluator
    from net.model import load_checkpoint

    import torch

    torch.set_num_threads(1)
    global _MODEL_CACHE
    try:
        model = _MODEL_CACHE[net_path]
    except (NameError, KeyError):
        model = load_checkpoint(net_path)
        _MODEL_CACHE = {net_path: model}
    net = NetEvaluator(model, prior_temperature=temp, value_blend=vblend)

    net_seat = seed % 2
    net_agent = MCTSEngine(
        simulations=sims, determinizations=dets, seed=seed,
        net=net, net_prior_mix=mix,
    )
    if opponent == "mcts":
        opp_agent = MCTSEngine(simulations=sims, determinizations=dets, seed=seed + 999)
    else:
        opp_agent = HeuristicAgent()
    seats = (net_agent, opp_agent) if net_seat == 0 else (opp_agent, net_agent)
    seats[0].begin_game(0)
    seats[1].begin_game(1)

    state = new_game(seed)
    n = 0
    t0 = time.time()
    while state.phase is not Phase.GAME_OVER and n < MAX_ACTIONS:
        action = seats[state.player_to_act()].select_action(state)
        apply_action(state, action)
        seats[0].observe(state, action)
        seats[1].observe(state, action)
        n += 1
    return {
        "seed": seed,
        "net_seat": net_seat,
        "net_won": state.winner == net_seat,
        "turns": state.turn_count,
        "secs": round(time.time() - t0, 1),
        "net": breakdown(state, net_seat),
        "opp": breakdown(state, 1 - net_seat),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--opponent", choices=("mcts", "heuristic"), default="mcts")
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--sims", type=int, default=160)
    ap.add_argument("--dets", type=int, default=4)
    # Measured-best ensemble calibration by default; --mix 1.0 --temp 1.0
    # --vblend 0.0 for a pure-net gate.
    ap.add_argument("--mix", type=float, default=0.3, help="net prior weight (1=pure)")
    ap.add_argument("--temp", type=float, default=0.5, help="prior temperature")
    ap.add_argument("--vblend", type=float, default=0.5, help="static-value blend")
    # Default gate seeds start far above any plausible training seed range:
    # evaluating on boards the net trained on contaminates the gate.
    ap.add_argument("--seed-offset", type=int, default=100_000)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    args = ap.parse_args()

    jobs = [
        (s + args.seed_offset, args.sims, args.dets, args.net, args.opponent,
         args.mix, args.temp, args.vblend)
        for s in range(args.games)
    ]
    results = []
    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        for r in pool.imap_unordered(play_one, jobs):
            results.append(r)
            w = sum(x["net_won"] for x in results)
            print(
                f"[{len(results)}/{args.games}] seed {r['seed']}: "
                f"{'WIN ' if r['net_won'] else 'loss'} "
                f"vps=({r['net']['total_vp']},{r['opp']['total_vp']}) "
                f"running {w}/{len(results)} ({time.time() - t0:.0f}s)",
                flush=True,
            )

    games = len(results)
    wins = sum(r["net_won"] for r in results)
    lo, hi = wilson_ci(wins, games)
    print(
        f"\nnet-MCTS vs {args.opponent} (equal s={args.sims},k={args.dets}): "
        f"{wins}/{games} = {wins / games:.1%} (95% CI {lo:.1%}-{hi:.1%})"
    )
    for seat in (0, 1):
        sub = [r for r in results if r["net_seat"] == seat]
        if sub:
            w = sum(r["net_won"] for r in sub)
            print(f"  as seat {seat}: {w}/{len(sub)}")


if __name__ == "__main__":
    main()
