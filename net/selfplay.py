"""Self-play data generation (PLAN.md Stage 5).

Both seats play with the MCTS engine (heuristic priors for gen-0; a net
checkpoint for later generations). Every multi-choice, codec-encodable
decision yields one training sample:

  features       actor-perspective encoding of the decision state
  policy target  root visit distribution over ActionCodec indices,
                 **noise-free and visit-floor pruned** (M5 fix #1): root
                 Dirichlet noise is OFF by default — measured gen-1 targets
                 averaged only 0.50 mass on the top action, the net
                 faithfully learned that flatness, and flat priors are weak
                 inside PUCT. Actions below ~1.5% of root visits are pruned
                 from the target and the rest renormalized (exploration
                 stubs are not policy signal).
  value target   0.5 * z + 0.5 * rootQ, both from the actor's perspective —
                 mixing the game outcome with the search's own root value
                 halves target variance at laptop-scale data sizes

Move selection: visit-proportional with temperature 1 for the first
TEMP_MOVES decisions (opening diversity, unaffected by target pruning),
argmax after. Board randomization + determinization sampling supply the
rest of the exploration; --root-noise restores the AlphaZero recipe for
experiments.

Usage: python -m net.selfplay --games 300 --out data/gen1.npz [--net ckpt.pt]
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import time

import numpy as np

from engine import Phase, apply_action, legal_actions, new_game
from search import MCTSEngine

from .codec import POLICY_SIZE, encode_action
from .encode import FEATURE_DIM, StateEncoder

MAX_ACTIONS = 6000
TEMP_MOVES = 20
TARGET_VISIT_FLOOR = 0.015  # prune actions below this share of root visits


def _engine(
    seed: int, sims: int, dets: int, net_path: str | None, root_noise: bool
) -> MCTSEngine:
    net = None
    if net_path:
        from .evaluator import get_evaluator

        net = get_evaluator(net_path)
    return MCTSEngine(
        simulations=sims,
        determinizations=dets,
        seed=seed,
        net=net,
        root_noise=root_noise,
    )


def _pruned_target(idxs: list[int], visits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Visit distribution with exploration stubs removed (M5 fix #1)."""
    floor = max(1.0, TARGET_VISIT_FLOOR * visits.sum())
    keep = visits > floor
    if not keep.any():
        keep = visits == visits.max()
    kept_idx = np.array([i for i, k in zip(idxs, keep) if k], dtype=np.int64)
    kept_val = visits[keep].astype(np.float32)
    return kept_idx, kept_val / kept_val.sum()


def selfplay_game(args: tuple) -> dict:
    """One self-play game -> sample arrays. Never raises: a failed game
    returns {"error": ...} so one bad game cannot kill a multi-hour pool."""
    try:
        return _selfplay_game(args)
    except Exception as exc:  # noqa: BLE001 - deliberate crash barrier
        return {"seed": args[0], "error": repr(exc)}


def _selfplay_game(args: tuple) -> dict:
    seed, sims, dets, net_path, root_noise, z_weight = args
    engines = [
        _engine(seed * 2 + i, sims, dets, net_path, root_noise) for i in (0, 1)
    ]
    for i, e in enumerate(engines):
        e.begin_game(i)
    encoder = StateEncoder()
    state = new_game(seed)

    feats: list[np.ndarray] = []
    pol_idx: list[np.ndarray] = []
    pol_val: list[np.ndarray] = []
    root_qs: list[float] = []
    actors: list[int] = []
    decision = 0
    n = 0
    import random as _random

    move_rng = _random.Random(seed + 424242)

    while state.phase is not Phase.GAME_OVER and n < MAX_ACTIONS:
        actor = state.player_to_act()
        actions = legal_actions(state)
        if len(actions) == 1:
            action = actions[0]
        else:
            evals = engines[actor].evaluate(state)
            idxs = [encode_action(e.action) for e in evals]
            visits = np.array([e.visits for e in evals], dtype=np.float64)
            if idxs[0] is not None and visits.sum() > 0:
                feats.append(encoder.encode(state, actor))
                kept_idx, kept_val = _pruned_target(idxs, visits)
                pol_idx.append(kept_idx)
                pol_val.append(kept_val)
                q = float(sum(e.visits * e.q for e in evals) / visits.sum())
                root_qs.append(q)
                actors.append(actor)
            if decision < TEMP_MOVES:
                probs = visits / visits.sum() if visits.sum() else None
                choice = (
                    move_rng.choices(range(len(evals)), weights=probs)[0]
                    if probs is not None
                    else 0
                )
                action = evals[choice].action
            else:
                action = evals[0].action
            decision += 1
        apply_action(state, action)
        for e in engines:
            e.observe(state, action)
        n += 1

    winner = state.winner
    # Draws (action-cap games with no winner) score 0.5 for both sides —
    # scoring them 0 for both would bias the value head downward.
    z = np.array(
        [1.0 if winner == a else (0.5 if winner is None else 0.0) for a in actors],
        dtype=np.float32,
    )
    q = np.array(root_qs, dtype=np.float32)
    # Auxiliary targets (M5 fix #6, KataGo-style): dense final-outcome
    # supervision per position — final VPs of both players and final award
    # ownership, from the sample actor's perspective. The value head was
    # measured to be game-outcome-starved (BCE flat at ~0.38 across
    # generations); these give it graded signal instead of one bit per game.
    final_vp = (state.total_vp(0), state.total_vp(1))
    lr, la = state.longest_road_holder, state.largest_army_holder
    aux = np.array(
        [
            [
                min(final_vp[a], 16) / 16.0,
                min(final_vp[1 - a], 16) / 16.0,
                float(lr == a),
                float(lr == 1 - a),
                float(la == a),
                float(la == 1 - a),
            ]
            for a in actors
        ],
        dtype=np.float32,
    ).reshape(len(actors), 6)
    # z_weight leans the value target on real outcomes vs the search's own
    # rootQ. The rootQ half is a variance reducer at small game counts, but
    # it anchors the value head to the static evaluator it must eventually
    # beat — at thousands of games, favor z (recipe uses 0.75).
    return {
        "seed": seed,
        "winner": winner,
        "turns": state.turn_count,
        "X": np.stack(feats) if feats else np.zeros((0, FEATURE_DIM), np.float32),
        "value_target": z_weight * z + (1.0 - z_weight) * q,
        "aux_target": aux,
        "pol_idx": pol_idx,   # list of per-sample index arrays
        "pol_val": pol_val,   # list of per-sample prob arrays
    }


def pack(results: list[dict], out_path: str) -> dict:
    """Concatenate per-game samples into one flat COO npz."""
    X = np.concatenate([r["X"] for r in results])
    value = np.concatenate([r["value_target"] for r in results])
    all_idx, all_val, ptr = [], [], [0]
    for r in results:
        for idx, val in zip(r["pol_idx"], r["pol_val"]):
            all_idx.append(idx)
            all_val.append(val)
            ptr.append(ptr[-1] + len(idx))
    arrays = dict(
        X=X,
        value_target=value,
        pol_ptr=np.array(ptr, dtype=np.int64),
        pol_idx=np.concatenate(all_idx) if all_idx else np.zeros(0, np.int64),
        pol_val=np.concatenate(all_val) if all_val else np.zeros(0, np.float32),
        policy_size=np.int64(POLICY_SIZE),
    )
    if all("seed" in r for r in results):
        # game id per sample: lets training split validation by GAME —
        # same-game samples are heavily correlated, so a per-sample split
        # leaks and makes early stopping optimistic.
        arrays["game_id"] = np.concatenate(
            [np.full(len(r["X"]), r["seed"], dtype=np.int64) for r in results]
        )
    if all("aux_target" in r for r in results):
        arrays["aux_target"] = np.concatenate([r["aux_target"] for r in results])
    np.savez_compressed(out_path, **arrays)
    return {"samples": len(X), "games": len(results)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=300)
    # Deeper searches -> sharper, more informative policy targets (M5 fix #4):
    # fewer games at more sims beats more games at fewer sims for target quality.
    ap.add_argument("--sims", type=int, default=400)
    ap.add_argument("--dets", type=int, default=4)
    ap.add_argument("--net", default=None, help="checkpoint for net-guided self-play")
    ap.add_argument("--root-noise", action="store_true",
                    help="restore AlphaZero Dirichlet root noise (off by default)")
    ap.add_argument("--z-weight", type=float, default=0.5,
                    help="value target = z_weight*outcome + (1-z_weight)*rootQ; "
                         "use 0.75+ for large runs")
    ap.add_argument("--seed-offset", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=max(2, mp.cpu_count() - 2))
    args = ap.parse_args()

    jobs = [
        (s + args.seed_offset, args.sims, args.dets, args.net, args.root_noise,
         args.z_weight)
        for s in range(args.games)
    ]
    results = []
    failures = 0
    done = 0
    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        for r in pool.imap_unordered(selfplay_game, jobs):
            done += 1
            if "error" in r:
                failures += 1
                print(f"[{done}/{args.games}] seed {r['seed']} FAILED: {r['error']}",
                      flush=True)
                continue
            results.append(r)
            print(
                f"[{done}/{args.games}] seed {r['seed']}: "
                f"winner={r['winner']} turns={r['turns']} "
                f"samples={len(r['X'])} ({time.time() - t0:.0f}s)",
                flush=True,
            )
    stats = pack(results, args.out)
    print(f"wrote {args.out}: {stats['samples']} samples from {stats['games']} games"
          + (f" ({failures} failed games skipped)" if failures else ""))


if __name__ == "__main__":
    main()
