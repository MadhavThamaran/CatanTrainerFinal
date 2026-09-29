"""Heuristic agent (PLAN.md Stage 3 / Stage A).

A non-ML evaluator strong enough to (a) rank placement-phase moves
credibly, (b) beat the random baseline decisively, and (c) serve as the
leaf/rollout policy for the Stage 4 MCTS engine. It is intentionally
greedy and myopic — no lookahead — but its position features (pip-weighted
production, diversity, ports, robber value) are the same features the
learned value net will later subsume.

`placement_value` / `production_value` are exposed at module and package
level because puzzle tooling and tests consume them directly.
"""
from __future__ import annotations

from engine import (
    Action,
    ActionType,
    Board,
    Building,
    GameState,
    PortType,
    Resource,
    TOPOLOGY,
    legal_actions,
)
from engine.longest_road import longest_road_length
from engine.rules import _legal_settlement_vertices  # reachability helper
from engine.types import COST_CITY, COST_SETTLEMENT, LARGEST_ARMY_MIN, LONGEST_ROAD_MIN

from .base import Agent

# Dice "pips": the number of the 36 combinations that make each total —
# i.e. the relative production frequency of a hex with that number.
PIP = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 0, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}

# Mild resource preferences: wheat is universally needed, ore drives cities
# and dev cards. Kept close to 1 so raw production dominates.
RESOURCE_WEIGHT = {
    Resource.WHEAT: 1.2,
    Resource.ORE: 1.1,
    Resource.WOOD: 1.0,
    Resource.BRICK: 1.0,
    Resource.SHEEP: 0.9,
}

DIVERSITY_BONUS = 2.0


# ---------------------------------------------------------------------------
# Position features (pure functions of the board)
# ---------------------------------------------------------------------------


def _resource_pips(board: Board, vertex: int) -> dict[Resource, int]:
    """Total pips per resource reachable from a settlement on `vertex`."""
    out: dict[Resource, int] = {}
    for h in TOPOLOGY.vertex_hexes[vertex]:
        n = board.numbers[h]
        res = board.terrain[h].resource
        if n is None or res is None:
            continue
        out[res] = out.get(res, 0) + PIP[n]
    return out


def production_value(board: Board, vertex: int) -> float:
    """Weighted expected production of a settlement on `vertex` (no diversity
    or port terms). A city doubles this."""
    return sum(RESOURCE_WEIGHT[r] * pips for r, pips in _resource_pips(board, vertex).items())


def _port_type_at(board: Board, vertex: int) -> PortType | None:
    for edge, port in board.ports.items():
        if vertex in TOPOLOGY.edge_vertices[edge]:
            return port
    return None


def placement_value(board: Board, vertex: int) -> float:
    """Full placement score: production + resource diversity + port synergy.
    This is the ranking used for both setup and mid-game settlement spots."""
    pips = _resource_pips(board, vertex)
    score = sum(RESOURCE_WEIGHT[r] * p for r, p in pips.items())
    score += DIVERSITY_BONUS * len(pips)

    port = _port_type_at(board, vertex)
    if port is PortType.GENERIC:
        score += 1.5
    elif port is not None:
        # A 2:1 port is worth more the more of that resource you produce here.
        score += 1.0 + 0.4 * pips.get(port.resource, 0)
    return score


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class HeuristicAgent(Agent):
    name = "heuristic"

    def select_action(self, state: GameState) -> Action:
        actions = legal_actions(state)
        if len(actions) == 1:
            return actions[0]  # forced (ROLL, single discard/road, ...)
        scores = self.score_actions(state, actions)
        return actions[max(range(len(actions)), key=scores.__getitem__)]

    def score_actions(self, state: GameState, actions: list[Action]) -> list[float]:
        """Raw heuristic score per action (same logic select_action ranks by).
        Also consumed by the search layer as a policy *prior* (PLAN.md §2.1:
        the heuristic fills the prior slot until the Stage 5 net replaces it)."""
        kind = actions[0].type
        if kind is ActionType.SETUP_PLACE_SETTLEMENT:
            return [self._setup_settlement_score(state, a) for a in actions]
        if kind is ActionType.SETUP_PLACE_ROAD:
            return [self._setup_road_score(state, a) for a in actions]
        if kind is ActionType.DISCARD:
            return [-self._discard_cost(a) for a in actions]
        if kind is ActionType.MOVE_ROBBER:
            return [self._robber_score(state, a) for a in actions]
        return [self._action_score(state, a) for a in actions]

    # --- setup ---

    def _setup_settlement_score(self, state: GameState, a: Action) -> float:
        """Placement value plus a bonus for covering resources the player's
        existing settlements don't yet produce (opening balance)."""
        score = placement_value(state.board, a.vertex)
        have = {
            r
            for v, (owner, _) in state.buildings.items()
            if owner == a.player
            for r in _resource_pips(state.board, v)
        }
        new = set(_resource_pips(state.board, a.vertex)) - have
        return score + 1.5 * len(new)

    def _setup_road_score(self, state: GameState, a: Action) -> float:
        """Point the setup road toward the best future settlement spot."""
        settlement = state.last_setup_settlement
        far = TOPOLOGY.edge_other_vertex(a.edge, settlement)
        # Best open vertex one road beyond the far end (can't settle adjacent
        # to our own settlement, so look past it).
        options = [
            placement_value(state.board, v)
            for v in TOPOLOGY.vertex_neighbors[far]
            if v != settlement
        ]
        return max(options, default=0.0)

    # --- discards ---

    def _discard_cost(self, a: Action) -> float:
        return sum(RESOURCE_WEIGHT[r] for r in a.resources)

    # --- robber ---

    def _robber_score(self, state: GameState, a: Action) -> float:
        me = state.current_player
        opp = 1 - me
        score = 0.0
        # hex_vertices (not vertex_hexes!): a.hex is a HEX id, so we need
        # the hex's own vertices to find buildings sitting on it.
        for v in TOPOLOGY.hex_vertices[a.hex]:
            b = state.buildings.get(v)
            if b is None:
                continue
            owner, kind = b
            n = state.board.numbers[a.hex]
            pips = PIP.get(n, 0) if n is not None else 0
            mult = 1 if kind is Building.SETTLEMENT else 2
            if owner == opp:
                score += pips * mult
            else:  # never volunteer to block our own production
                score -= pips * mult
        # Prefer a hex where a steal actually lands.
        opp_adjacent = any(
            state.buildings.get(v, (None,))[0] == opp
            for v in TOPOLOGY.hex_vertices[a.hex]
        )
        if opp_adjacent and state.players[opp].hand_size() > 0:
            score += 4.0
        return score

    # --- main action phase ---

    def _action_score(self, state: GameState, a: Action) -> float:
        p = a.player
        t = a.type
        if t is ActionType.END_TURN:
            return 0.0
        if t is ActionType.BUILD_CITY:
            return 100.0 + 0.5 * production_value(state.board, a.vertex)
        if t is ActionType.BUILD_SETTLEMENT:
            return 60.0 + placement_value(state.board, a.vertex)
        if t is ActionType.BUILD_ROAD:
            return self._road_score(state, a)
        if t is ActionType.BUY_DEV_CARD:
            return self._dev_buy_score(state, p)
        if t is ActionType.PLAY_KNIGHT:
            return self._knight_score(state, a)
        if t is ActionType.PLAY_ROAD_BUILDING:
            return 28.0  # ~ two free roads; good while expanding
        if t is ActionType.PLAY_YEAR_OF_PLENTY:
            return self._year_of_plenty_score(state, a)
        if t is ActionType.PLAY_MONOPOLY:
            return self._monopoly_score(state, a)
        if t is ActionType.TRADE_BANK:
            return self._trade_score(state, a)
        return 0.0

    def _road_score(self, state: GameState, a: Action) -> float:
        """Value a road by the best of: opening a new settlement spot, gaining
        or extending toward Longest Road (a 2 VP swing), or — failing both — a
        small nudge so the network still grows when nothing better exists."""
        p = a.player
        opp = 1 - p
        before = set(_legal_settlement_vertices(state, p))
        before_len = longest_road_length(state, p)
        state.roads[a.edge] = p  # temp mutate, restored below
        after = set(_legal_settlement_vertices(state, p))
        after_len = longest_road_length(state, p)
        del state.roads[a.edge]

        score = 3.0
        opened = after - before
        if opened:
            score = 30.0 + 0.7 * max(placement_value(state.board, v) for v in opened)

        if after_len > before_len:
            holder = state.longest_road_holder
            opp_len = longest_road_length(state, opp)
            if after_len >= LONGEST_ROAD_MIN and holder != p and after_len > opp_len:
                score = max(score, 45.0)                     # take the 2 VP award
            elif holder == p and after_len > opp_len:
                score = max(score, 8.0)                      # defend the lead
            elif after_len == LONGEST_ROAD_MIN - 1 and holder != p:
                score = max(score, 14.0)                     # one road from claiming it
        return score

    def _dev_buy_score(self, state: GameState, player: int) -> float:
        """Dev cards give a mix of direct VP, Largest-Army knights, and tempo.
        Worth more when Largest Army is a live 2 VP swing we are within reach
        of (buy knights to contest it), otherwise a steady mid-priority buy."""
        opp = 1 - player
        my_k = state.players[player].knights_played
        opp_k = state.players[opp].knights_played
        needed_for_la = max(LARGEST_ARMY_MIN, opp_k + 1)
        if state.largest_army_holder != player and my_k >= needed_for_la - 2:
            return 34.0  # within ~2 knights of taking Largest Army — chase it
        return 22.0

    def _knight_score(self, state: GameState, a: Action) -> float:
        p = a.player
        opp = 1 - p
        ps = state.players[p]
        would = ps.knights_played + 1
        # Playing this knight seizes/holds Largest Army (a 2 VP swing).
        holder = state.largest_army_holder
        if would >= 3 and holder != p and would > state.players[opp].knights_played:
            return 50.0
        if state.robber_hex in _our_production_hexes(state, p):
            return 20.0  # knock the robber off our own production
        if state.visible_vp(opp) > 2 and state.players[opp].hand_size() > 0:
            return 15.0  # a live steal
        return 8.0

    def _year_of_plenty_score(self, state: GameState, a: Action) -> float:
        ps = state.players[a.player]
        hand = dict(ps.resources)
        before_city = _affordable(hand, COST_CITY)
        before_settle = _affordable(hand, COST_SETTLEMENT)
        for r in a.resources:
            hand[r] += 1
        if _affordable(hand, COST_CITY) and not before_city:
            return 45.0
        if _affordable(hand, COST_SETTLEMENT) and not before_settle:
            return 42.0
        return -1.0  # otherwise hold the card

    def _monopoly_score(self, state: GameState, a: Action) -> float:
        held = state.players[1 - a.player].resources[a.get]
        if held >= 4:
            return 30.0 + held
        if held >= 3:
            return 15.0
        return -1.0  # not worth burning the card

    def _trade_score(self, state: GameState, a: Action) -> float:
        """Trade to complete a build now (best), or to make steady progress
        toward a build target by converting genuine surplus into a resource
        the target still needs. Never trade away a resource the target
        needs, and never trade speculatively."""
        p = a.player
        ratio = state.trade_ratio(p, a.give)
        hand = dict(state.players[p].resources)
        before_city = _affordable(hand, COST_CITY)
        before_settle = _affordable(hand, COST_SETTLEMENT)
        hand[a.give] -= ratio
        hand[a.get] += 1
        if _affordable(hand, COST_CITY) and not before_city:
            return 55.0
        if _affordable(hand, COST_SETTLEMENT) and not before_settle:
            return 50.0

        # Progress trade toward a target we can eventually complete.
        target = self._build_target(state, p)
        if target is None:
            return -1.0
        current = state.players[p].resources
        need_get = max(0, target.get(a.get, 0) - current[a.get])
        keep_give = target.get(a.give, 0)
        surplus_ok = current[a.give] - ratio >= keep_give
        if need_get > 0 and surplus_ok:
            return 8.0  # above a pointless road (3), below buying a dev card (25)
        return -1.0

    def _build_target(self, state: GameState, player: int) -> dict[Resource, int] | None:
        """Cheapest concrete build the player should stockpile toward: a city
        if any settlement can be upgraded, else a settlement."""
        ps = state.players[player]
        has_settlement = any(
            owner == player and kind is Building.SETTLEMENT
            for owner, kind in state.buildings.values()
        )
        if ps.cities_left > 0 and has_settlement:
            return COST_CITY
        if ps.settlements_left > 0:
            return COST_SETTLEMENT
        return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _affordable(hand: dict[Resource, int], cost: dict[Resource, int]) -> bool:
    return all(hand[r] >= n for r, n in cost.items())


def _our_production_hexes(state: GameState, player: int) -> set[int]:
    return {
        h
        for v, (owner, _) in state.buildings.items()
        if owner == player
        for h in TOPOLOGY.vertex_hexes[v]
    }
