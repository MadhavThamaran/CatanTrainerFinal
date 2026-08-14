"""Lockstep batched net evaluation (M5 next lever #2).

`MCTSEngine` with a net now steps its K determinized trees together and
batches their leaf evaluations into one forward per step. The searches
themselves must be unchanged: same determinizations, same per-tree RNG
streams, same tree decisions. This compares the lockstep engine against an
inline reimplementation of the old sequential loop on real decision states.
"""
import random

import pytest

pytest.importorskip("torch")

from engine import Phase, apply_action, legal_actions, new_game
from net.evaluator import NetEvaluator
from net.model import GraphPolicyValueNet
from search import MCTSEngine
from search.determinize import Determinizer
from search.mcts import MCTS

SIMS, DETS, SEED = 24, 3, 0


def _decision_state(seed, plies):
    state = new_game(seed)
    rng = random.Random(seed)
    for _ in range(plies):
        if state.phase is Phase.GAME_OVER:
            break
        apply_action(state, rng.choice(legal_actions(state)))
    while state.phase is not Phase.GAME_OVER and len(legal_actions(state)) < 5:
        apply_action(state, rng.choice(legal_actions(state)))
    return None if state.phase is Phase.GAME_OVER else state


def _net():
    import torch

    torch.manual_seed(0)
    return NetEvaluator(GraphPolicyValueNet(d=32, rounds=2).eval())


def _sequential_reference(state, net):
    """The pre-lockstep engine loop, verbatim: one tree at a time, one net
    call per node."""
    viewer = state.player_to_act()
    actions = legal_actions(state)
    rng = random.Random(SEED)
    determinizer = Determinizer(None)
    totals = {a: [0, 0.0] for a in actions}
    for _ in range(DETS):
        det = determinizer.sample(state, viewer, rng)
        tree = MCTS(
            det,
            rng=random.Random(rng.randrange(2**63)),
            rollout_depth=0,
            net=net,
        )
        tree.run(SIMS)
        for action, (n, q0) in tree.root_stats().items():
            q = q0 if viewer == 0 else 1.0 - q0
            acc = totals[action]
            acc[0] += n
            acc[1] += n * q
    return {a: (n, s / n if n else 0.5) for a, (n, s) in totals.items()}


@pytest.mark.parametrize("seed,plies", [(3, 40), (7, 120), (11, 200)])
def test_lockstep_matches_sequential_search(seed, plies):
    state = _decision_state(seed, plies)
    if state is None:
        pytest.skip("game ended before a wide decision point")
    net = _net()
    reference = _sequential_reference(state, net)
    engine = MCTSEngine(
        simulations=SIMS, determinizations=DETS, seed=SEED, net=net
    )
    evals = engine.evaluate(state.clone())
    assert sum(e.visits for e in evals) == sum(n for n, _ in reference.values())
    for e in evals:
        n_ref, q_ref = reference[e.action]
        assert e.visits == n_ref, f"{e.action}: {e.visits} != {n_ref}"
        assert abs(e.q - q_ref) < 1e-6, f"{e.action}: {e.q} != {q_ref}"


@pytest.mark.slow
def test_lockstep_full_game_terminates():
    """Net-guided self-play to a real 15-VP finish with the champion
    checkpoint (end-of-game expansions exercise the terminal_node path in
    the lockstep driver). A random-weight net stalls games, so this uses
    the trained net; slow-marked accordingly."""
    from net.evaluator import get_evaluator

    net = get_evaluator("checkpoints/gen4_blind.pt")
    engines = [
        MCTSEngine(simulations=12, determinizations=2, seed=s, net=net)
        for s in (1, 2)
    ]
    for i, e in enumerate(engines):
        e.begin_game(i)
    state = new_game(9)
    n = 0
    while state.phase is not Phase.GAME_OVER and n < 4000:
        actions = legal_actions(state)
        action = (
            actions[0]
            if len(actions) == 1
            else engines[state.player_to_act()].evaluate(state)[0].action
        )
        for e in engines:
            e.observe(state, action)
        apply_action(state, action)
        n += 1
    assert state.phase is Phase.GAME_OVER, "game did not finish"
