"""PUCT search over one determinized world (PLAN.md Stage 4).

AlphaZero-shaped MCTS specialized for this engine:

- **Prior-guided selection (PUCT).** Wide decision fans (the 54-vertex
  setup placements, rich mid-game action sets) are steered by a policy
  prior — currently a softmax over the Stage 3 heuristic's action scores;
  the Stage 5 network drops into the same slot. Narrow fans use uniform
  priors (computing the heuristic there costs more than it buys).
- **Chance via outcome-keyed children.** Stochastic actions (dice ROLL,
  robber steal) sample their outcome inside `apply_action` from the state's
  own controllers — including the real BalancedDice distribution. Each
  (action, observed-outcome) pair gets its own child node, so chance is
  sampled at the true frequencies with zero explicit chance-node math.
  Within a determinization the dev deck order is fixed, so BUY_DEV_CARD is
  deterministic here.
- **Per-action stats.** A node aggregates (visits, value) per *action*
  across that action's outcome children — exactly the per-move numbers the
  tactics trainer consumes at the root.
- **Values are P(player 0 wins)** in [0, 1]; selection flips perspective for
  the acting player at each node, which handles the interleaved forced
  sub-decisions (discards, robber moves) of both players transparently.
- **Leaf evaluation:** truncated build-greedy rollout, then the static
  `win_prob_p0` evaluator; terminal results decay gently with depth so a
  win now outranks the same win later.
- **Pruning:** at internal nodes a huge DISCARD fan is cut to the
  keep-value-best few; the root is never pruned (puzzle labeling needs every
  legal move scored).
"""
from __future__ import annotations

import math
import random

from engine import Action, ActionType, GameState, Phase, apply_action, legal_actions

from agents.heuristic import HeuristicAgent, RESOURCE_WEIGHT

from .value import win_prob_p0

_DISCARD_CAP = 12          # internal-node fan-out cap for discard combos
# Terminal values decay gently toward 0.5 with depth so a win NOW strictly
# outranks the same win a few actions later — the defining preference of a
# tactics engine. Static leaf values already carry uncertainty (clamped to
# [0.02, 0.98]) and are not decayed.
_TERMINAL_GAMMA = 0.998
# Heuristic priors are computed only where they pay for themselves: setup
# placements and wide action fans. Narrow nodes get uniform priors.
_PRIOR_MIN_ACTIONS = 10
# Softmax over Z-SCORED heuristic scores: scale-free, so placement values
# (~8-45) and action-phase scores (0-100) both yield usefully sharp priors.
_PRIOR_TEMPERATURE = 0.8
# First-play urgency: unvisited actions inherit the node's running value
# minus a reduction, instead of a flat optimistic 0.5.
_FPU_REDUCTION = 0.15
_ROLLOUT_PRIORITY = {
    ActionType.BUILD_CITY: 0,
    ActionType.BUILD_SETTLEMENT: 1,
    ActionType.BUILD_ROAD: 2,
    ActionType.BUY_DEV_CARD: 3,
}


class _Node:
    __slots__ = ("actions", "priors", "stats", "children", "value0")

    def __init__(
        self,
        actions: list[Action],
        priors: list[float],
        value0: float | None = None,
    ):
        self.actions = actions
        self.priors = priors                      # aligned with actions
        self.stats: dict[Action, list] = {}       # action -> [visits, total_w]
        self.children: dict[tuple, _Node] = {}    # (action, outcome) -> node
        self.value0 = value0                      # net leaf value, p0 view


class MCTS:
    """One tree over one determinized root state."""

    def __init__(
        self,
        root_state: GameState,
        rng: random.Random,
        c_puct: float = 1.5,
        rollout_depth: int = 16,
        net=None,                 # NetEvaluator | None: fills prior+leaf slots
        root_noise: bool = False,  # Dirichlet noise for self-play exploration
        use_net_priors: bool = True,   # diagnostics: net fills the prior slot
        use_net_value: bool = True,    # diagnostics: net fills the leaf slot
        net_prior_mix: float = 1.0,    # 1 = pure net priors, 0 = pure heuristic
        defer_root: bool = False,  # lockstep driver builds the root itself
    ):
        self._root_state = root_state
        self._rng = rng
        self._c = c_puct
        self._rollout_depth = rollout_depth
        self._net = net
        self._use_net_priors = use_net_priors
        self._use_net_value = use_net_value
        self._net_prior_mix = net_prior_mix
        self._root_noise = root_noise
        self._prior_agent = HeuristicAgent()
        # Root keeps the FULL legal action set (labeling contract) and always
        # gets real priors.
        self._root = None if defer_root else self._make_node(
            root_state, force_priors=True
        )
        if self._root is not None:
            self._finish_root()

    def _finish_root(self) -> None:
        if self._root_noise and len(self._root.actions) > 1:
            self._mix_root_noise()

    def _mix_root_noise(self, eps: float = 0.25, alpha: float = 0.3) -> None:
        noise = [self._rng.gammavariate(alpha, 1.0) for _ in self._root.priors]
        total = sum(noise) or 1.0
        self._root.priors = [
            (1 - eps) * p + eps * (g / total)
            for p, g in zip(self._root.priors, noise)
        ]

    def run(self, simulations: int) -> None:
        for _ in range(simulations):
            self._simulate()

    # --- lockstep interface (batched net evaluation across trees) ---
    #
    # A driver owning several independent trees steps them together:
    # `start_simulation` descends to a leaf and returns the pending
    # expansion (or None if the simulation completed at a terminal); the
    # driver batches all pending `node_request`s through one net forward,
    # then hands each tree its built node via `finish_simulation`. Per-tree
    # results are identical to sequential `run()` — only the net batching
    # changes.

    @property
    def root_state(self) -> GameState:
        return self._root_state

    def root_request(self) -> tuple[list[Action], int] | None:
        return self.node_request(self._root_state)

    def set_root(self, node: _Node) -> None:
        self._root = node
        self._finish_root()

    def terminal_node(self) -> _Node:
        return _Node([], [])

    def start_simulation(self):
        """Descend once. Returns None if the simulation finished (terminal),
        else an opaque pending tuple for `finish_simulation`."""
        path, state, parent, key = self._descend()
        if parent is None:
            return None
        return path, state, parent, key

    def finish_simulation(self, pending, node: _Node) -> None:
        path, state, parent, key = pending
        self._attach_leaf(path, state, parent, key, node)

    def root_stats(self) -> dict[Action, tuple[int, float]]:
        """action -> (visits, mean value for player 0)."""
        return {
            a: (n, w / n if n else 0.0) for a, (n, w) in self._root.stats.items()
        }

    # --- one simulation ---

    def _simulate(self) -> None:
        path, state, parent, key = self._descend()
        if parent is None:
            return  # terminal descent: already backed up
        # First visit to this action or a fresh chance outcome: expand a
        # leaf here and evaluate it (net value when a net is attached, else
        # rollout + static evaluator).
        child = self._make_node(state)
        self._attach_leaf(path, state, parent, key, child)

    def _descend(self):
        """Walk to a leaf. Terminal descents back up immediately and return
        (path, state, None, None); otherwise a new node must be built from
        `state` and attached at (parent, key) via `_attach_leaf`."""
        state = self._root_state.clone()
        node = self._root
        path: list[tuple[_Node, Action]] = []

        while True:
            if state.phase is Phase.GAME_OVER:
                self._backprop(path, self._terminal_value(state, len(path)))
                return path, state, None, None
            action, is_new = self._puct_pick(state, node)
            key = self._apply(state, action)
            if is_new:
                node.stats[action] = [0, 0.0]
            path.append((node, action))
            child = node.children.get(key)
            if child is None:
                return path, state, node, key
            node = child

    def _attach_leaf(self, path, state, parent, key, child: _Node) -> None:
        parent.children[key] = child
        if child.value0 is not None:
            value = child.value0
        else:
            value = self._leaf_value(state, len(path))
        self._backprop(path, value)

    def _backprop(self, path, value: float) -> None:
        for n, a in path:
            st = n.stats[a]
            st[0] += 1
            st[1] += value

    def _apply(self, state: GameState, action: Action) -> tuple:
        """Apply and return the (action, outcome) child key. Outcomes that are
        random *within* a determinization: dice totals and stolen cards."""
        if action.type is ActionType.ROLL:
            apply_action(state, action)
            return (action, state.last_roll)
        if action.type is ActionType.MOVE_ROBBER:
            before = tuple(state.players[action.player].resources.values())
            apply_action(state, action)
            after = tuple(state.players[action.player].resources.values())
            stolen = after != before and tuple(
                b - a for a, b in zip(before, after)
            )
            return (action, stolen or None)
        apply_action(state, action)
        return (action, None)

    def _make_node(self, state: GameState, force_priors: bool = False) -> _Node:
        req = self.node_request(state)
        if req is None:
            return _Node([], [])
        actions, actor = req
        if self._net is not None:
            priors, value_actor = self._net.evaluate(state, actions, actor)
            return self.build_node(
                state, actions, actor, priors, value_actor, force_priors
            )
        return _Node(actions, self._priors(state, actions, force=force_priors))

    def node_request(self, state: GameState) -> tuple[list[Action], int] | None:
        """The (actions, actor) a net evaluation of this node needs, or None
        when the node is terminal (buildable without one). Split from
        `_make_node` so the lockstep driver in `search/engine.py` can batch
        the net calls of many trees into one forward."""
        if state.phase is Phase.GAME_OVER:
            return None
        actions = legal_actions(state)
        if (
            len(actions) > _DISCARD_CAP
            and actions[0].type is ActionType.DISCARD
        ):
            # Keep the combos that throw away the least keep-value.
            actions = sorted(
                actions,
                key=lambda a: sum(RESOURCE_WEIGHT[r] for r in a.resources),
            )[:_DISCARD_CAP]
        return actions, state.player_to_act()

    def build_node(
        self,
        state: GameState,
        actions: list[Action],
        actor: int,
        priors: list[float] | None,
        value_actor: float,
        force_priors: bool = False,
    ) -> _Node:
        """Assemble a node from net outputs (the tail of `_make_node`)."""
        value0 = (
            (value_actor if actor == 0 else 1.0 - value_actor)
            if self._use_net_value
            else None
        )
        if priors is None or not self._use_net_priors:
            priors = self._priors(state, actions, force=force_priors)
        elif self._net_prior_mix < 1.0:
            # Prior ensembling: an early-generation net knows placement
            # and build preferences but cannot yet discriminate the
            # *context* for roads/expansion the way the heuristic's
            # hand-crafted scores do. Mix, don't replace.
            w = self._net_prior_mix
            heur = self._priors(state, actions, force=force_priors)
            mixed = [w * pn + (1 - w) * ph for pn, ph in zip(priors, heur)]
            total = sum(mixed)
            priors = [p / total for p in mixed]
        return _Node(actions, priors, value0)

    def _priors(
        self, state: GameState, actions: list[Action], force: bool = False
    ) -> list[float]:
        """Softmax over heuristic action scores; uniform on narrow fans
        (where the heuristic costs more than it buys) unless forced."""
        n = len(actions)
        if n == 0:
            return []
        if n < _PRIOR_MIN_ACTIONS and not force:
            return [1.0 / n] * n
        scores = self._prior_agent.score_actions(state, actions)
        mean = sum(scores) / n
        var = sum((s - mean) ** 2 for s in scores) / n
        std = math.sqrt(var)
        if std < 1e-9:
            return [1.0 / n] * n
        top = max(scores)
        exps = [
            math.exp(((s - top) / std) / _PRIOR_TEMPERATURE) for s in scores
        ]
        total = sum(exps)
        return [e / total for e in exps]

    def _puct_pick(self, state: GameState, node: _Node) -> tuple[Action, bool]:
        """PUCT over ALL actions (visited or not); returns (action, is_new)."""
        actor = state.player_to_act()
        total_n = 0
        total_w = 0.0
        for n, w in node.stats.values():
            total_n += n
            total_w += w
        if total_n:
            node_q0 = total_w / total_n
            node_q = node_q0 if actor == 0 else 1.0 - node_q0
        else:
            node_q = 0.5
        # First-play urgency: unvisited actions start slightly below the
        # node's running value, so unexplored moves aren't optimistically
        # preferred in losing positions.
        fpu = max(0.0, node_q - _FPU_REDUCTION)
        sqrt_total = math.sqrt(total_n + 1)
        best, best_score, best_new = None, -math.inf, False
        for action, prior in zip(node.actions, node.priors):
            st = node.stats.get(action)
            if st is None:
                q, n, is_new = fpu, 0, True
            else:
                n, w = st
                q0 = w / n
                q = q0 if actor == 0 else 1.0 - q0
                is_new = False
            score = q + self._c * prior * sqrt_total / (1 + n)
            if score > best_score:
                best, best_score, best_new = action, score, is_new
        return best, best_new

    def _terminal_value(self, state: GameState, depth: int) -> float:
        v = win_prob_p0(state)  # exactly 0.0 or 1.0 at terminals
        return 0.5 + (v - 0.5) * (_TERMINAL_GAMMA ** depth)

    def _leaf_value(self, state: GameState, depth: int) -> float:
        """Truncated build-greedy rollout, then static evaluation (terminal
        results decay with total depth, see _TERMINAL_GAMMA)."""
        rng = self._rng
        for step in range(self._rollout_depth):
            if state.phase is Phase.GAME_OVER:
                return self._terminal_value(state, depth + step)
            actions = legal_actions(state)
            builds = [a for a in actions if a.type in _ROLLOUT_PRIORITY]
            if builds:
                best = min(_ROLLOUT_PRIORITY[a.type] for a in builds)
                pool = [a for a in builds if _ROLLOUT_PRIORITY[a.type] == best]
            else:
                pool = [a for a in actions if a.type is not ActionType.TRADE_BANK] or actions
            apply_action(state, rng.choice(pool))
        if state.phase is Phase.GAME_OVER:
            return self._terminal_value(state, depth + self._rollout_depth)
        return win_prob_p0(state)
