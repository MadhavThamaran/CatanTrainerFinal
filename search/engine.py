"""`MCTSEngine.evaluate(state) -> ranked per-move scores` — the product
primitive (PLAN.md Stage 4; frozen interface from here on).

Root-level determinization (PIMC): sample K complete worlds consistent with
the acting player's information set, run an independent UCT search in each,
then aggregate per-action root statistics visit-weighted across worlds.
Q-values are win probabilities from the acting player's perspective, so
`Q(best) - Q(move)` is the regret ("centipawn loss") the trainer's scoring
consumes (PLAN.md Stage 6).

Also an `Agent`, so it drops straight into the Stage 3 harness; when played
as an agent it feeds a `CardTracker` via the harness observe hook, making
its determinizations exact-belief rather than prior-based.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from engine import Action, GameState, legal_actions

from agents.base import Agent

from .belief import CardTracker
from .determinize import Determinizer
from .mcts import MCTS


@dataclass(frozen=True)
class MoveEval:
    action: Action
    q: float        # win prob for the player to act; 0.5 if never visited
    visits: int


class MCTSEngine(Agent):
    """Determinized-MCTS move evaluator.

    Defaults are a playable middle ground; puzzle labeling should raise
    `simulations`/`determinizations` well above them (offline budget is
    cheap). `simulations` is per determinization.
    """

    def __init__(
        self,
        simulations: int = 160,
        determinizations: int = 4,
        rollout_depth: int = 0,
        c_puct: float = 1.5,
        seed: int = 0,
        net=None,                  # NetEvaluator: replaces prior + leaf slots
        root_noise: bool = False,  # Dirichlet root noise (self-play only)
        use_net_priors: bool = True,
        use_net_value: bool = True,
        net_prior_mix: float = 1.0,
        exact: bool = False,       # ANALYSIS_SPEC §4: state is already perfect,
                                    # skip hidden-info resampling (see Determinizer)
    ):
        # rollout_depth=0 (pure static leaf) measured STRONGER than
        # truncated random-greedy rollouts at equal wall time: the weak
        # rollout policy injects more noise than signal, while the static
        # evaluator (search/value.py) is deterministic. Rollouts remain
        # available for experiments.
        self.simulations = simulations
        self.determinizations = determinizations
        self.rollout_depth = rollout_depth
        self.c_puct = c_puct
        self.net = net
        self.root_noise = root_noise
        self.use_net_priors = use_net_priors
        self.use_net_value = use_net_value
        self.net_prior_mix = net_prior_mix
        self.exact = exact
        self._master_seed = seed
        self._rng = random.Random(seed)
        self._tracker: CardTracker | None = None
        tag = "+net" if net is not None else ""
        self.name = f"mcts{tag}(s={simulations},k={determinizations},d={rollout_depth})"

    # --- the product primitive ---

    def evaluate(self, state: GameState, viewer: int | None = None) -> list[MoveEval]:
        """Rank every legal move of the player to act, best first."""
        if viewer is None:
            viewer = state.player_to_act()
        actions = legal_actions(state)
        if len(actions) == 1:
            return [MoveEval(actions[0], 0.5, 0)]

        determinizer = Determinizer(
            self._tracker if self._tracker and self._tracker.viewer == viewer else None,
            exact=self.exact,
        )
        totals: dict[Action, list] = {a: [0, 0.0] for a in actions}  # [N, sum(N*q)]
        trees = [
            MCTS(
                determinizer.sample(state, viewer, self._rng),
                rng=random.Random(self._rng.randrange(2**63)),
                c_puct=self.c_puct,
                rollout_depth=self.rollout_depth,
                net=self.net,
                root_noise=self.root_noise,
                use_net_priors=self.use_net_priors,
                use_net_value=self.use_net_value,
                net_prior_mix=self.net_prior_mix,
                defer_root=self.net is not None,
            )
            for _ in range(self.determinizations)
        ]
        if self.net is not None:
            self._run_lockstep(trees)
        else:
            for tree in trees:
                tree.run(self.simulations)
        for tree in trees:
            for action, (n, q0) in tree.root_stats().items():
                q = q0 if viewer == 0 else 1.0 - q0
                acc = totals[action]
                acc[0] += n
                acc[1] += n * q

        evals = [
            MoveEval(a, (s / n) if n else 0.5, n) for a, (n, s) in totals.items()
        ]
        # Rank by visit count first (the standard robust MCTS choice: visits
        # are prior- and value-guided), Q as tie-break. Ranking by raw Q would
        # let an unvisited action's neutral 0.5 outrank real moves whenever
        # the position is losing. At labeling budgets the orderings converge.
        evals.sort(key=lambda e: (e.visits, e.q), reverse=True)
        return evals

    def _run_lockstep(self, trees: list[MCTS]) -> None:
        """Step the K determinized trees together, batching their net
        evaluations into one forward per step (root construction included).
        Per-tree search is identical to sequential `tree.run(simulations)`
        — trees are independent and keep their own RNGs; only the net-call
        batching changes. Profiling showed batch-1 forward dispatch
        dominating search wall time (~85%)."""
        # Batched root construction.
        live = []
        for t in trees:
            req = t.root_request()
            if req is None:
                t.set_root(t.terminal_node())
            else:
                live.append((t, req))
        if live:
            results = self.net.evaluate_batch(
                [t.root_state for t, _ in live],
                [r[0] for _, r in live],
                [r[1] for _, r in live],
            )
            for (t, (acts, actor)), (priors, value) in zip(live, results):
                t.set_root(
                    t.build_node(
                        t.root_state, acts, actor, priors, value, force_priors=True
                    )
                )

        for _ in range(self.simulations):
            pending = []
            for tree in trees:
                p = tree.start_simulation()
                if p is None:
                    continue  # terminal descent: already backed up
                state = p[1]
                req = tree.node_request(state)
                if req is None:
                    # The applied action ended the game: empty node, and the
                    # backup value comes from the terminal state (same as
                    # the sequential path).
                    tree.finish_simulation(p, tree.terminal_node())
                    continue
                pending.append((tree, p, state, req))
            if not pending:
                continue
            results = self.net.evaluate_batch(
                [state for _, _, state, _ in pending],
                [req[0] for _, _, _, req in pending],
                [req[1] for _, _, _, req in pending],
            )
            for (tree, p, state, (acts, actor)), (priors, value) in zip(
                pending, results
            ):
                tree.finish_simulation(
                    p, tree.build_node(state, acts, actor, priors, value)
                )

    # --- Agent interface ---

    def begin_game(self, seat: int) -> None:
        self._tracker = CardTracker(viewer=seat)
        self._rng = random.Random(self._master_seed + 7919 * seat)

    def observe(self, state: GameState, action: Action) -> None:
        if self._tracker is not None:
            self._tracker.observe(state, action)

    def select_action(self, state: GameState) -> Action:
        return self.evaluate(state)[0].action
