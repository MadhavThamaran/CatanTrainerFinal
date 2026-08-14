"""Opponent-hand belief tracking (PLAN.md Stage 4).

In this no-trade variant nearly every hand event is publicly inferable:
production is public (board + dice), every build/buy/bank-trade has a known
cost, Monopoly/Year-of-Plenty reveal themselves, and both directions of
robber steals are seen by the viewer (they either watch the card leave their
own hand or see what they drew). The ONLY hidden event is the composition of
an opponent discard on a 7.

`CardTracker` therefore maintains an exact per-resource count of the
opponent's hand and stays **exact until the opponent's first hidden
discard**; from there it degrades gracefully to a proportional estimate
(tracked via `exact`). The determinizer uses `sample_hand()` to draw
concrete opponent hands for search.

Feed it every applied action via `observe(post_state, action)` — the
harness's `Agent.observe` hook does exactly this. It never reads the
opponent's true hand; it recomputes everything from public information plus
the viewer's own cards.
"""
from __future__ import annotations

import random

from engine import Action, ActionType, Building, GameState, Resource, TOPOLOGY
from engine.types import COST_CITY, COST_DEV_CARD, COST_ROAD, COST_SETTLEMENT

from agents.heuristic import PIP

_COSTS = {
    ActionType.BUILD_ROAD: COST_ROAD,
    ActionType.BUILD_SETTLEMENT: COST_SETTLEMENT,
    ActionType.BUILD_CITY: COST_CITY,
    ActionType.BUY_DEV_CARD: COST_DEV_CARD,
}


class CardTracker:
    """Tracks the opponent's resource hand from player `viewer`'s viewpoint."""

    def __init__(self, viewer: int):
        self.viewer = viewer
        self.opp = 1 - viewer
        self.known: dict[Resource, int] = {r: 0 for r in Resource}
        self.exact = True
        self._own_snapshot: dict[Resource, int] | None = None
        self._opp_free_roads = 0  # pending free placements from Road Building

    # --- event feed ---

    def observe(self, state: GameState, action: Action) -> None:
        """Update from a just-applied action. `state` is the post-action
        perfect state, but only public info + the viewer's own hand are read."""
        own_now = dict(state.players[self.viewer].resources)
        own_before = self._own_snapshot or own_now
        t, actor = action.type, action.player

        if t is ActionType.ROLL and state.last_roll is not None and state.last_roll != 7:
            self._add(self._production(state, state.last_roll))
        elif t is ActionType.DISCARD and actor == self.opp:
            self._hidden_discard(len(action.resources))
        elif t in (ActionType.MOVE_ROBBER,):
            if actor == self.opp:
                # Opponent stole from us: the card that left our hand is known.
                for r in Resource:
                    lost = own_before[r] - own_now[r]
                    if lost > 0:
                        self.known[r] += lost
            else:
                # We stole from them: we see what we drew.
                for r in Resource:
                    gained = own_now[r] - own_before[r]
                    if gained > 0:
                        self._remove(r, gained)
        elif actor == self.opp:
            if t is ActionType.PLAY_ROAD_BUILDING:
                self._opp_free_roads = state.free_roads  # granted count, public
            elif t is ActionType.BUILD_ROAD and self._opp_free_roads > 0:
                self._opp_free_roads = state.free_roads  # free placement, no cost
            elif t is ActionType.END_TURN:
                self._opp_free_roads = 0
            elif t in _COSTS:
                for r, n in _COSTS[t].items():
                    self._remove(r, n)
            elif t is ActionType.SETUP_PLACE_SETTLEMENT:
                self._setup_gain(state, action)
            elif t is ActionType.TRADE_BANK:
                # Ratio is inferable from the opponent's public port access.
                ratio = state.trade_ratio(self.opp, action.give)
                self._remove(action.give, ratio)
                self.known[action.get] += 1
            elif t is ActionType.PLAY_YEAR_OF_PLENTY:
                for r in action.resources:
                    self.known[r] += 1
            elif t is ActionType.PLAY_MONOPOLY:
                # They took everything we held of that resource — public.
                taken = own_before[action.get] - own_now[action.get]
                self.known[action.get] += taken
        elif actor == self.viewer and t is ActionType.PLAY_MONOPOLY:
            # We drained their declared resource entirely.
            self.known[action.get] = 0
            self._reconcile(state)

        self._own_snapshot = own_now
        self._check(state)

    # --- sampling (for the determinizer) ---

    def believed_total(self) -> int:
        return sum(self.known.values())

    def sample_hand(self, state: GameState, rng: random.Random) -> dict[Resource, int]:
        """A concrete opponent hand consistent with the public hand size.
        Exact mode returns the tracked hand; otherwise the tracked counts are
        adjusted (production-weighted) to match the public count."""
        target = state.players[self.opp].hand_size()
        hand = dict(self.known)
        diff = target - sum(hand.values())
        weights = _production_weights(state, self.opp)
        while diff > 0:
            hand[_weighted_pick(weights, rng)] += 1
            diff -= 1
        while diff < 0:
            pool = [r for r in Resource for _ in range(hand[r])]
            hand[rng.choice(pool)] -= 1
            diff += 1
        return hand

    # --- internals ---

    def _add(self, gains: dict[Resource, int]) -> None:
        for r, n in gains.items():
            self.known[r] += n

    def _remove(self, r: Resource, n: int) -> None:
        if self.known[r] >= n:
            self.known[r] -= n
        else:
            # Our belief was short (only possible after a hidden discard):
            # they demonstrably held it, so absorb the error.
            self.known[r] = 0
            self.exact = False

    def _hidden_discard(self, k: int) -> None:
        """Opponent discarded k cards of hidden composition: remove k
        proportionally (largest remainder). Exactness is lost."""
        total = self.believed_total()
        if total <= 0:
            return
        removed = 0
        shares = sorted(
            ((self.known[r] * k / total, r) for r in Resource),
            key=lambda x: x[0],
            reverse=True,
        )
        for share, r in shares:
            take = min(self.known[r], int(share))
            self.known[r] -= take
            removed += take
        i = 0
        order = [r for _, r in shares]
        while removed < k and any(self.known[r] > 0 for r in Resource):
            r = order[i % len(order)]
            if self.known[r] > 0:
                self.known[r] -= 1
                removed += 1
            i += 1
        self.exact = False

    def _production(self, state: GameState, total: int) -> dict[Resource, int]:
        gains: dict[Resource, int] = {}
        for h in state.board.hexes_with_number(total):
            if h == state.robber_hex:
                continue
            res = state.board.terrain[h].resource
            if res is None:
                continue
            for v in TOPOLOGY.hex_vertices[h]:
                b = state.buildings.get(v)
                if b is not None and b[0] == self.opp:
                    gains[res] = gains.get(res, 0) + (
                        1 if b[1] is Building.SETTLEMENT else 2
                    )
        return gains

    def _setup_gain(self, state: GameState, action: Action) -> None:
        # Second setup settlement grants its adjacent resources (public).
        if state.setup_index >= 2:
            for h in TOPOLOGY.vertex_hexes[action.vertex]:
                res = state.board.terrain[h].resource
                if res is not None:
                    self.known[res] += 1

    def _reconcile(self, state: GameState) -> None:
        if self.believed_total() != state.players[self.opp].hand_size():
            self.exact = False

    def _check(self, state: GameState) -> None:
        # Public hand size is ground truth; drift means a hidden event
        # slipped past our model — flag, never crash.
        if self.exact and self.believed_total() != state.players[self.opp].hand_size():
            self.exact = False


def _production_weights(state: GameState, player: int) -> dict[Resource, float]:
    """Prior over unknown cards: proportional to the player's pip production
    (plus smoothing so zero-production resources stay possible)."""
    w = {r: 0.5 for r in Resource}
    for v, (owner, kind) in state.buildings.items():
        if owner != player:
            continue
        mult = 1 if kind is Building.SETTLEMENT else 2
        for h in TOPOLOGY.vertex_hexes[v]:
            n = state.board.numbers[h]
            res = state.board.terrain[h].resource
            if n is not None and res is not None:
                w[res] += mult * PIP[n]
    return w


def _weighted_pick(weights: dict[Resource, float], rng: random.Random) -> Resource:
    total = sum(weights.values())
    x = rng.uniform(0.0, total)
    cum = 0.0
    for r, w in weights.items():
        cum += w
        if x <= cum:
            return r
    return list(weights)[-1]
