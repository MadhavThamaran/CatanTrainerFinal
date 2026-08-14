"""Rules engine: `legal_actions(state)` and `apply_action(state, action)`.

This pair is the entire engine API. `apply_action` mutates the state (use
`state.clone()` first if you need the original) and trusts that the action
came from `legal_actions`; cheap assertions guard the invariants that are
easy to get wrong, full validation is the caller's job.

Spec references (docs/rules.md) are cited inline.
"""
from __future__ import annotations

from itertools import combinations_with_replacement

from .actions import Action, ActionType
from .longest_road import longest_road_length
from .state import SETUP_ORDER, GameState
from .topology import TOPOLOGY
from .types import (
    COST_CITY,
    COST_DEV_CARD,
    COST_ROAD,
    COST_SETTLEMENT,
    DISCARD_THRESHOLD,
    LARGEST_ARMY_MIN,
    LONGEST_ROAD_MIN,
    ROBBER_VISIBLE_VP_PROTECTION,
    VP_TO_WIN,
    Building,
    DevCard,
    Phase,
    Resource,
)

# ---------------------------------------------------------------------------
# Legal actions
# ---------------------------------------------------------------------------


def legal_actions(state: GameState) -> list[Action]:
    if state.phase is Phase.GAME_OVER:
        return []
    if state.phase is Phase.SETUP:
        return _setup_actions(state)

    p = state.player_to_act()
    if state.pending_discards:
        return _discard_actions(state, p)
    if state.pending_robber:
        return [
            Action(ActionType.MOVE_ROBBER, p, hex=h)
            for h in robber_destinations(state)
        ]
    if state.needs_roll:
        return [Action(ActionType.ROLL, p)]
    if state.free_roads > 0:
        # Road Building placements are forced while any remain (apply clears
        # the counter as soon as no placement is possible).
        return [
            Action(ActionType.BUILD_ROAD, p, edge=e)
            for e in _legal_road_edges(state, p)
        ]
    return _action_phase_actions(state, p)


def _setup_actions(state: GameState) -> list[Action]:
    p = SETUP_ORDER[state.setup_index]
    if state.awaiting_setup_road:
        v = state.last_setup_settlement
        assert v is not None
        return [
            Action(ActionType.SETUP_PLACE_ROAD, p, edge=e)
            for e in TOPOLOGY.vertex_edges[v]
            if e not in state.roads
        ]
    return [
        Action(ActionType.SETUP_PLACE_SETTLEMENT, p, vertex=v)
        for v in range(TOPOLOGY.num_vertices)
        if _vertex_placeable(state, v)
    ]


def _vertex_placeable(state: GameState, v: int) -> bool:
    """Empty and distance rule (rules.md §5.2): all neighbors empty too."""
    if v in state.buildings:
        return False
    return all(nb not in state.buildings for nb in TOPOLOGY.vertex_neighbors[v])


def _discard_actions(state: GameState, p: int) -> list[Action]:
    hand = state.players[p].resources
    k = state.players[p].hand_size() // 2  # discard half, rounded down (§2.2)
    return [
        Action(ActionType.DISCARD, p, resources=combo)
        for combo in _multiset_combinations(hand, k)
    ]


def _multiset_combinations(
    hand: dict[Resource, int], k: int
) -> list[tuple[Resource, ...]]:
    resources = list(Resource)
    out: list[tuple[Resource, ...]] = []

    def rec(i: int, remaining: int, chosen: list[Resource]) -> None:
        if remaining == 0:
            out.append(tuple(chosen))
            return
        if i == len(resources):
            return
        r = resources[i]
        for take in range(min(hand.get(r, 0), remaining), -1, -1):
            rec(i + 1, remaining - take, chosen + [r] * take)

    rec(0, k, [])
    return out


def _friendly_destinations(state: GameState) -> list[int]:
    """Robber destinations that respect Friendly Robber (rules.md §9.2):
    exclude hexes adjacent to any opponent who is still protected
    (visible VP <= 2). May be empty."""
    mover = state.current_player
    protected_hexes: set[int] = set()
    for opp in range(len(state.players)):
        if opp == mover:
            continue
        if state.visible_vp(opp) <= ROBBER_VISIBLE_VP_PROTECTION:
            for v, (owner, _) in state.buildings.items():
                if owner == opp:
                    protected_hexes.update(TOPOLOGY.vertex_hexes[v])
    return [
        h
        for h in range(TOPOLOGY.num_hexes)
        if h != state.robber_hex and h not in protected_hexes
    ]


def robber_destinations(state: GameState) -> list[int]:
    """Friendly set if non-empty, else any non-current hex with no steal
    (spec §9.2 fallback)."""
    friendly = _friendly_destinations(state)
    if friendly:
        return friendly
    return [h for h in range(TOPOLOGY.num_hexes) if h != state.robber_hex]


def _action_phase_actions(state: GameState, p: int) -> list[Action]:
    ps = state.players[p]
    actions: list[Action] = [Action(ActionType.END_TURN, p)]

    if ps.roads_left > 0 and _can_afford(ps.resources, COST_ROAD):
        actions += [
            Action(ActionType.BUILD_ROAD, p, edge=e)
            for e in _legal_road_edges(state, p)
        ]
    if ps.settlements_left > 0 and _can_afford(ps.resources, COST_SETTLEMENT):
        actions += [
            Action(ActionType.BUILD_SETTLEMENT, p, vertex=v)
            for v in _legal_settlement_vertices(state, p)
        ]
    if ps.cities_left > 0 and _can_afford(ps.resources, COST_CITY):
        actions += [
            Action(ActionType.BUILD_CITY, p, vertex=v)
            for v, (owner, kind) in state.buildings.items()
            if owner == p and kind is Building.SETTLEMENT
        ]
    if state.dev_deck and _can_afford(ps.resources, COST_DEV_CARD):
        actions.append(Action(ActionType.BUY_DEV_CARD, p))

    # One non-VP dev card per turn; nothing bought this turn (rules.md §8.6, §7.4).
    if not state.dev_played_this_turn:
        if ps.playable_dev_count(DevCard.KNIGHT) > 0:
            actions.append(Action(ActionType.PLAY_KNIGHT, p))
        if (
            ps.playable_dev_count(DevCard.ROAD_BUILDING) > 0
            and ps.roads_left > 0
            and _legal_road_edges(state, p)  # requires >= 1 legal road (§8.2)
        ):
            actions.append(Action(ActionType.PLAY_ROAD_BUILDING, p))
        if ps.playable_dev_count(DevCard.YEAR_OF_PLENTY) > 0:
            actions += [
                Action(ActionType.PLAY_YEAR_OF_PLENTY, p, resources=pair)
                for pair in combinations_with_replacement(list(Resource), 2)
            ]
        if ps.playable_dev_count(DevCard.MONOPOLY) > 0:
            actions += [
                Action(ActionType.PLAY_MONOPOLY, p, get=r) for r in Resource
            ]

    # Maritime trade only (rules.md §10): 4:1 bank, 3:1/2:1 via owned ports.
    for give in Resource:
        ratio = state.trade_ratio(p, give)
        if ps.resources[give] >= ratio:
            actions += [
                Action(ActionType.TRADE_BANK, p, give=give, get=get)
                for get in Resource
                if get is not give
            ]
    return actions


def _legal_road_edges(state: GameState, p: int) -> list[int]:
    """Unoccupied edges connected to p's network. Connection through a
    vertex occupied by an opponent building does not count (rules.md §7.1)."""
    out = []
    for e in range(TOPOLOGY.num_edges):
        if e in state.roads:
            continue
        for v in TOPOLOGY.edge_vertices[e]:
            b = state.buildings.get(v)
            if b is not None:
                if b[0] == p:
                    out.append(e)
                    break
                continue  # opponent building blocks connection through v
            if any(
                state.roads.get(e2) == p
                for e2 in TOPOLOGY.vertex_edges[v]
                if e2 != e
            ):
                out.append(e)
                break
    return out


def _legal_settlement_vertices(state: GameState, p: int) -> list[int]:
    return [
        v
        for v in range(TOPOLOGY.num_vertices)
        if _vertex_placeable(state, v)
        and any(state.roads.get(e) == p for e in TOPOLOGY.vertex_edges[v])
    ]


def _can_afford(resources: dict[Resource, int], cost: dict[Resource, int]) -> bool:
    return all(resources[r] >= n for r, n in cost.items())


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def apply_action(state: GameState, action: Action) -> None:
    assert state.phase is not Phase.GAME_OVER, "game is over"
    assert action.player == state.player_to_act(), "acting out of turn"
    handler = _HANDLERS[action.type]
    handler(state, action)


def _pay(state: GameState, p: int, cost: dict[Resource, int]) -> None:
    ps = state.players[p]
    for r, n in cost.items():
        assert ps.resources[r] >= n, f"cannot pay {n} {r}"
        ps.resources[r] -= n
    # Bank is infinite (recommended_engine_mode_v1, rules.md §10.4): paid
    # cards vanish; granted cards appear. Finite-bank mode would hook here.


def _check_win(state: GameState) -> None:
    """First to VP_TO_WIN on their own turn (rules.md §2.1). Only the
    current player's total can cross the threshold mid-turn."""
    p = state.current_player
    if state.phase is Phase.MAIN and state.total_vp(p) >= VP_TO_WIN:
        state.winner = p
        state.phase = Phase.GAME_OVER


def _update_longest_road(state: GameState) -> None:
    """Recompute after any road placement or any settlement placement (a new
    opponent settlement can cut a road). Tie -> holder retains; tie with no
    holder -> nobody (rules.md §11.1)."""
    lengths = [longest_road_length(state, p) for p in range(len(state.players))]
    holder = state.longest_road_holder
    if holder is not None:
        others = max(l for p, l in enumerate(lengths) if p != holder)
        if lengths[holder] >= LONGEST_ROAD_MIN and lengths[holder] >= others:
            return  # holder retains (including on ties)
        holder = None
    candidates = [p for p, l in enumerate(lengths) if l >= LONGEST_ROAD_MIN]
    if candidates:
        best = max(lengths[p] for p in candidates)
        leaders = [p for p in candidates if lengths[p] == best]
        holder = leaders[0] if len(leaders) == 1 else None  # tie, no holder -> nobody
    state.longest_road_holder = holder


def _update_largest_army(state: GameState, p: int) -> None:
    """Tie -> holder retains (rules.md §12.1)."""
    holder = state.largest_army_holder
    if holder == p:
        return
    k = state.players[p].knights_played
    if k >= LARGEST_ARMY_MIN and (
        holder is None or k > state.players[holder].knights_played
    ):
        state.largest_army_holder = p


# --- setup phase ---


def _apply_setup_settlement(state: GameState, a: Action) -> None:
    assert _vertex_placeable(state, a.vertex)
    state.buildings[a.vertex] = (a.player, Building.SETTLEMENT)
    state.players[a.player].settlements_left -= 1
    state.last_setup_settlement = a.vertex
    state.awaiting_setup_road = True
    # Starting resources from the second settlement only (rules.md §5.4):
    # placements 2 and 3 of the A-B-B-A order are each player's second.
    if state.setup_index >= 2:
        for h in TOPOLOGY.vertex_hexes[a.vertex]:
            res = state.board.terrain[h].resource
            if res is not None:
                state.players[a.player].resources[res] += 1


def _apply_setup_road(state: GameState, a: Action) -> None:
    assert a.edge not in state.roads
    assert state.last_setup_settlement in TOPOLOGY.edge_vertices[a.edge]
    state.roads[a.edge] = a.player
    state.players[a.player].roads_left -= 1
    state.awaiting_setup_road = False
    state.setup_index += 1
    if state.setup_index == len(SETUP_ORDER):
        state.phase = Phase.MAIN
        state.current_player = 0
        state.needs_roll = True


# --- turn skeleton ---


def _apply_roll(state: GameState, a: Action) -> None:
    total = state.dice.next_roll(a.player)
    state.last_roll = total
    state.needs_roll = False
    if total == 7:
        state.pending_discards = [
            p
            for p in range(len(state.players))
            if state.players[p].hand_size() > DISCARD_THRESHOLD
        ]
        state.pending_robber = True
    else:
        _produce(state, total)


def _produce(state: GameState, total: int) -> None:
    for h in state.board.hexes_with_number(total):
        if h == state.robber_hex:  # robber blocks production (rules.md §9.1)
            continue
        res = state.board.terrain[h].resource
        if res is None:
            continue
        for v in TOPOLOGY.hex_vertices[h]:
            b = state.buildings.get(v)
            if b is not None:
                owner, kind = b
                state.players[owner].resources[res] += (
                    1 if kind is Building.SETTLEMENT else 2
                )


def _apply_discard(state: GameState, a: Action) -> None:
    assert a.player == state.pending_discards[0]
    ps = state.players[a.player]
    assert len(a.resources) == ps.hand_size() // 2
    for r in a.resources:
        assert ps.resources[r] > 0
        ps.resources[r] -= 1
    state.pending_discards.pop(0)


def _apply_move_robber(state: GameState, a: Action) -> None:
    assert a.hex != state.robber_hex  # must move (rules.md §9.3)
    steal_allowed = a.hex in _friendly_destinations(state)
    state.robber_hex = a.hex
    state.pending_robber = False
    if not steal_allowed:
        return
    # 1v1: single opponent, victim choice is automatic. Steal one uniform
    # random card if the opponent has a building on the hex and any cards.
    mover = state.current_player
    opp = 1 - mover
    adjacent = any(
        state.buildings.get(v, (None,))[0] == opp
        for v in TOPOLOGY.hex_vertices[a.hex]
    )
    if adjacent and state.players[opp].hand_size() > 0:
        hand = [
            r for r, n in state.players[opp].resources.items() for _ in range(n)
        ]
        stolen = state.rng.choice(hand)
        state.players[opp].resources[stolen] -= 1
        state.players[mover].resources[stolen] += 1


def _apply_end_turn(state: GameState, a: Action) -> None:
    ps = state.players[a.player]
    ps.dev_bought_this_turn = {c: 0 for c in DevCard}
    state.dev_played_this_turn = False
    state.free_roads = 0
    state.last_roll = None
    state.current_player = 1 - state.current_player
    state.needs_roll = True
    state.turn_count += 1


# --- builds / buys ---


def _apply_build_road(state: GameState, a: Action) -> None:
    assert a.edge not in state.roads
    if state.free_roads > 0:
        state.free_roads -= 1
    else:
        _pay(state, a.player, COST_ROAD)
    state.roads[a.edge] = a.player
    state.players[a.player].roads_left -= 1
    _update_longest_road(state)
    # Clear leftover free placements that can no longer be used.
    if state.free_roads > 0 and (
        state.players[a.player].roads_left == 0
        or not _legal_road_edges(state, a.player)
    ):
        state.free_roads = 0
    _check_win(state)


def _apply_build_settlement(state: GameState, a: Action) -> None:
    assert _vertex_placeable(state, a.vertex)
    _pay(state, a.player, COST_SETTLEMENT)
    state.buildings[a.vertex] = (a.player, Building.SETTLEMENT)
    state.players[a.player].settlements_left -= 1
    _update_longest_road(state)  # may cut the opponent's road
    _check_win(state)


def _apply_build_city(state: GameState, a: Action) -> None:
    owner, kind = state.buildings[a.vertex]
    assert owner == a.player and kind is Building.SETTLEMENT
    _pay(state, a.player, COST_CITY)
    state.buildings[a.vertex] = (a.player, Building.CITY)
    state.players[a.player].cities_left -= 1
    state.players[a.player].settlements_left += 1  # piece returns to supply
    _check_win(state)


def _apply_buy_dev_card(state: GameState, a: Action) -> None:
    _pay(state, a.player, COST_DEV_CARD)
    card = state.dev_deck.pop()
    ps = state.players[a.player]
    ps.dev_cards[card] += 1
    ps.dev_bought_this_turn[card] += 1
    _check_win(state)  # a drawn VP card can win immediately (rules.md §8.5)


# --- development card plays ---


def _play_dev(state: GameState, p: int, card: DevCard) -> None:
    ps = state.players[p]
    assert ps.playable_dev_count(card) > 0, "card unavailable this turn"
    assert not state.dev_played_this_turn, "one dev card per turn"
    ps.dev_cards[card] -= 1
    state.dev_played_this_turn = True


def _apply_play_knight(state: GameState, a: Action) -> None:
    _play_dev(state, a.player, DevCard.KNIGHT)
    state.players[a.player].knights_played += 1
    _update_largest_army(state, a.player)
    state.pending_robber = True
    _check_win(state)  # Largest Army can be the winning 2 VP


def _apply_play_road_building(state: GameState, a: Action) -> None:
    assert _legal_road_edges(state, a.player), "needs >= 1 legal road (rules.md §8.2)"
    _play_dev(state, a.player, DevCard.ROAD_BUILDING)
    state.free_roads = min(2, state.players[a.player].roads_left)


def _apply_play_year_of_plenty(state: GameState, a: Action) -> None:
    assert len(a.resources) == 2
    _play_dev(state, a.player, DevCard.YEAR_OF_PLENTY)
    for r in a.resources:
        state.players[a.player].resources[r] += 1


def _apply_play_monopoly(state: GameState, a: Action) -> None:
    _play_dev(state, a.player, DevCard.MONOPOLY)
    opp = 1 - a.player
    taken = state.players[opp].resources[a.get]
    state.players[opp].resources[a.get] = 0
    state.players[a.player].resources[a.get] += taken


# --- trade ---


def _apply_trade_bank(state: GameState, a: Action) -> None:
    ratio = state.trade_ratio(a.player, a.give)
    _pay(state, a.player, {a.give: ratio})
    state.players[a.player].resources[a.get] += 1


_HANDLERS = {
    ActionType.SETUP_PLACE_SETTLEMENT: _apply_setup_settlement,
    ActionType.SETUP_PLACE_ROAD: _apply_setup_road,
    ActionType.ROLL: _apply_roll,
    ActionType.END_TURN: _apply_end_turn,
    ActionType.DISCARD: _apply_discard,
    ActionType.MOVE_ROBBER: _apply_move_robber,
    ActionType.BUILD_ROAD: _apply_build_road,
    ActionType.BUILD_SETTLEMENT: _apply_build_settlement,
    ActionType.BUILD_CITY: _apply_build_city,
    ActionType.BUY_DEV_CARD: _apply_buy_dev_card,
    ActionType.PLAY_KNIGHT: _apply_play_knight,
    ActionType.PLAY_ROAD_BUILDING: _apply_play_road_building,
    ActionType.PLAY_YEAR_OF_PLENTY: _apply_play_year_of_plenty,
    ActionType.PLAY_MONOPOLY: _apply_play_monopoly,
    ActionType.TRADE_BANK: _apply_trade_bank,
}
