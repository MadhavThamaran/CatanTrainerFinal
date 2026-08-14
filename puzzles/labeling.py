"""Deep labeling + puzzle admission (PLAN.md Stage 6).

A candidate becomes a puzzle only if it survives:

  stability   the same best move under LABEL_SEEDS independent deep
              searches (different search RNG + determinization draws)
  clarity     Q(best) - Q(2nd) >= min_gap: a real best move exists
  undecided   the CHOICE matters: not hopeless (Q(best) > 0.05) and not
              won-regardless (best and 2nd both > 0.97). A position whose
              best move wins outright while others don't is the Catan
              "mate in one" — prime puzzle material, admitted.
  real fan    >= 3 legal moves

Q-values are visit-weighted across the stability runs. Moves the search
never visited at all (PUCT deliberately starves clearly-bad siblings of a
crushing move — sharp puzzles hit this by construction) get a one-step
static-evaluation fallback: coarse, but such moves live deep in the blunder
band where coarse is enough. Labeling runs offline — budgets here are
deliberately far above play budgets.
"""
from __future__ import annotations

from engine import GameState, apply_action
from net.codec import encode_action
from search import MCTSEngine
from search.value import win_prob_p0

from .schema import Puzzle, PuzzleMove, puzzle_id
from .scoring import points_for_regret

LABEL_SEEDS = (11, 22, 33)


def _net(net_path: str | None):
    if net_path is None:
        return None
    from net.evaluator import get_evaluator  # lazy: keeps torch optional

    return get_evaluator(net_path)


def label_candidate(
    candidate: dict,
    sims: int = 960,
    dets: int = 8,
    min_gap: float = 0.04,
    seeds: tuple = LABEL_SEEDS,
    net_path: str | None = None,
) -> tuple[Puzzle | None, str]:
    """Return (puzzle, "admitted") or (None, rejection_reason).

    `net_path` switches the labeling engine to net-guided search (gated by
    PLAN: only after a champion clears the raw engine decisively; audited
    via scripts/relabel_check.py). Recorded in label_config for provenance.
    """
    actor = candidate["actor"]
    runs = []
    for s in seeds:
        state = GameState.from_dict(candidate["state"], seed=s)
        engine = MCTSEngine(
            simulations=sims, determinizations=dets, seed=s, net=_net(net_path)
        )
        runs.append(engine.evaluate(state, viewer=actor))

    legal = {e.action for e in runs[0]}
    if len(legal) < 3:
        return None, "too-few-moves"

    tops = {run[0].action for run in runs}
    if len(tops) != 1:
        return None, "unstable-best"

    # Visit-weighted aggregation across runs (a run where a move went
    # unvisited reports a meaningless neutral q; weighting by visits
    # excludes it instead of polluting the mean).
    qv_sum: dict = {}
    visit_sum: dict = {}
    for run in runs:
        for e in run:
            qv_sum[e.action] = qv_sum.get(e.action, 0.0) + e.q * e.visits
            visit_sum[e.action] = visit_sum.get(e.action, 0) + e.visits
    q_of = {}
    for a in legal:
        if visit_sum[a] > 0:
            q_of[a] = qv_sum[a] / visit_sum[a]
        else:
            q_of[a] = _one_step_estimate(candidate["state"], a, actor)
    ranked = sorted(legal, key=lambda a: (q_of[a], visit_sum[a]), reverse=True)
    best = ranked[0]
    q_best = q_of[best]
    gap = q_best - q_of[ranked[1]]

    q_second = q_of[ranked[1]]
    if q_best < 0.05:
        return None, "decided"          # hopeless: every move loses
    if q_best > 0.97 and q_second > 0.97:
        return None, "decided"          # won regardless: the choice is moot
    if gap < min_gap:
        return None, "no-clear-best"

    moves = [
        PuzzleMove(
            codec_id=encode_action(a),
            action=repr(a),
            q=round(q_of[a], 4),
            visits=visit_sum[a],
            points=points_for_regret(q_best - q_of[a]),
            rank=rank,
        )
        for rank, a in enumerate(ranked)
    ]
    followup = None
    if candidate["phase"] == "placement":
        followup = _label_setup_road(candidate, best, sims, dets, seeds, net_path)
    puzzle = Puzzle(
        id=puzzle_id(candidate["state"], actor),
        phase=candidate["phase"],
        actor=actor,
        state=candidate["state"],
        moves=moves,
        best_codec_id=moves[0].codec_id,
        gap=round(gap, 4),
        difficulty=_difficulty(gap),
        explanation=_explain(candidate["phase"], moves, gap),
        label_config={
            "sims": sims,
            "dets": dets,
            "seeds": list(seeds),
            "net": net_path,
        },
        followup=followup,
    )
    return puzzle, "admitted"


def _label_setup_road(
    candidate: dict, best_settlement, sims, dets, seeds, net_path=None
) -> dict | None:
    """Label the setup-ROAD decision that follows the BEST settlement — the
    engine's line, so placement puzzles play settlement + road as one
    composite (the road is scored when the user found the best settlement).

    Full budget, all seeds: at half-sims/2-seeds the road Q-noise exceeded
    TIE_EPSILON and a median 2-of-3 roads scored 100 — direction barely
    mattered. Roads differ by less win-prob than settlements, so they need
    MORE resolution, not less."""
    from engine import apply_action

    base = GameState.from_dict(candidate["state"], seed=71)
    apply_action(base, best_settlement)
    actor = candidate["actor"]
    runs = [
        MCTSEngine(
            simulations=sims,
            determinizations=dets,
            seed=s,
            net=_net(net_path),
        ).evaluate(base, viewer=actor)
        for s in seeds
    ]
    legal = {e.action for e in runs[0]}
    if len(legal) < 2:
        return None  # single forced road: nothing to test
    qv: dict = {}
    vis: dict = {}
    for run in runs:
        for e in run:
            qv[e.action] = qv.get(e.action, 0.0) + e.q * e.visits
            vis[e.action] = vis.get(e.action, 0) + e.visits
    q_of = {
        a: (qv[a] / vis[a]) if vis[a] > 0 else 0.0 for a in legal
    }
    ranked = sorted(legal, key=lambda a: (q_of[a], vis[a]), reverse=True)
    q_best = q_of[ranked[0]]
    return {
        "parent_codec_id": encode_action(best_settlement),
        "state": base.to_dict(),
        "moves": [
            {
                "codec_id": encode_action(a),
                "action": repr(a),
                "q": round(q_of[a], 4),
                "visits": vis[a],
                "points": points_for_regret(q_best - q_of[a]),
                "rank": rank,
            }
            for rank, a in enumerate(ranked)
        ],
        "best_codec_id": encode_action(ranked[0]),
        "gap": round(q_best - q_of[ranked[1]], 4),
    }


def _one_step_estimate(state_dict: dict, action, actor: int) -> float:
    """Fallback label for a search-starved move: apply it and read the
    static evaluator, averaged over a few reconstructions (steal randomness
    etc.). Coarse by design — these moves are in the blunder band."""
    total = 0.0
    n = 3
    for s in range(n):
        state = GameState.from_dict(state_dict, seed=1000 + s)
        apply_action(state, action)
        wp0 = win_prob_p0(state)
        total += wp0 if actor == 0 else 1.0 - wp0
    return total / n


def _difficulty(gap: float) -> str:
    # Crude proxy: a wide margin usually means a findable tactic. Refine
    # with real user data in M7 (puzzle-Elo supersedes this).
    if gap >= 0.15:
        return "easy"
    if gap >= 0.08:
        return "medium"
    return "hard"


def _explain(phase: str, moves: list[PuzzleMove], gap: float) -> str:
    """One-line template. Full feature-diff / LLM explanations are M8."""
    best, second = moves[0], moves[1]
    lead = {
        "placement": "Best placement",
        "robber": "Best robber move",
        "endgame": "Best move in the race",
        "devcard": "Best move (dev-card timing)",
        "trade": "Best move (trade line)",
        "midgame": "Best move",
    }.get(phase, "Best move")
    return (
        f"{lead}: {best.action} — estimated {best.q:.0%} win chance, "
        f"{gap:.0%} ahead of the next option ({second.action})."
    )
