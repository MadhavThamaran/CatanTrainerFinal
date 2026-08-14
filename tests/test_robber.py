"""Friendly Robber (rules.md §9): players at <= 2 visible VP cannot be
robbed or blocked; hidden VP cards do not count toward visibility."""
from engine import (
    Action,
    ActionType,
    DevCard,
    TOPOLOGY,
    apply_action,
    legal_actions,
    robber_destinations,
)
from helpers import give, make_main_state, put_settlement


def _hexes_adjacent_to_player(state, player):
    return {
        h
        for v, (owner, _) in state.buildings.items()
        if owner == player
        for h in TOPOLOGY.vertex_hexes[v]
    }


def test_protected_opponent_hexes_are_excluded():
    state = make_main_state()
    put_settlement(state, 1, 0)
    put_settlement(state, 1, 10)
    assert state.visible_vp(1) == 2  # protected (<= 2)

    dests = set(robber_destinations(state))
    assert state.robber_hex not in dests  # must move (rules.md §9.3)
    assert not dests & _hexes_adjacent_to_player(state, 1)
    assert dests  # plenty of legal hexes remain


def test_hidden_vp_does_not_lift_protection():
    state = make_main_state()
    put_settlement(state, 1, 0)
    put_settlement(state, 1, 10)
    state.players[1].dev_cards[DevCard.VICTORY_POINT] = 5  # total VP 7, visible 2
    assert state.total_vp(1) == 7
    assert state.visible_vp(1) == 2
    assert not set(robber_destinations(state)) & _hexes_adjacent_to_player(state, 1)


def test_eligible_opponent_can_be_robbed():
    state = make_main_state()
    # Three settlements -> 3 visible VP -> robber-eligible.
    verts = [0, 10, 20]
    for v in verts:
        put_settlement(state, 1, v)
    assert state.visible_vp(1) == 3
    give(state, 1, wheat=1)

    target_hex = TOPOLOGY.vertex_hexes[verts[0]][0]
    if target_hex == state.robber_hex:
        target_hex = TOPOLOGY.vertex_hexes[verts[0]][-1]
    state.pending_robber = True
    assert target_hex in {a.hex for a in legal_actions(state)}

    apply_action(state, Action(ActionType.MOVE_ROBBER, 0, hex=target_hex))
    assert state.robber_hex == target_hex
    assert state.players[1].hand_size() == 0  # card stolen
    assert state.players[0].hand_size() == 1


def test_no_steal_when_opponent_not_adjacent():
    state = make_main_state()
    put_settlement(state, 1, 0)
    put_settlement(state, 1, 10)
    put_settlement(state, 1, 20)  # eligible, but we rob elsewhere
    give(state, 1, ore=2)
    away = next(
        h
        for h in robber_destinations(state)
        if h not in _hexes_adjacent_to_player(state, 1)
    )
    state.pending_robber = True
    apply_action(state, Action(ActionType.MOVE_ROBBER, 0, hex=away))
    assert state.players[1].hand_size() == 2
    assert state.players[0].hand_size() == 0
