"""Maritime trading only (rules.md §10): 4:1 bank, 3:1 generic port, 2:1
resource port, port access via buildings on port vertices, and no
player-to-player trade anywhere in the action space."""
from engine import (
    Action,
    ActionType,
    PortType,
    Resource,
    apply_action,
    legal_actions,
)
from helpers import give, make_board, make_main_state, put_settlement


def _port_vertex(board, port_type):
    edge = next(e for e, p in board.ports.items() if p is port_type)
    return board.port_vertices(edge)[0]


def test_bank_trade_is_four_to_one_without_ports():
    state = make_main_state()
    give(state, 0, wood=4)
    trades = [a for a in legal_actions(state) if a.type is ActionType.TRADE_BANK]
    assert {a.give for a in trades} == {Resource.WOOD}
    assert {a.get for a in trades} == set(Resource) - {Resource.WOOD}

    apply_action(state, Action(ActionType.TRADE_BANK, 0, give=Resource.WOOD, get=Resource.ORE))
    assert state.players[0].resources[Resource.WOOD] == 0
    assert state.players[0].resources[Resource.ORE] == 1


def test_three_cards_cannot_bank_trade_without_port():
    state = make_main_state()
    give(state, 0, wood=3)
    assert not any(a.type is ActionType.TRADE_BANK for a in legal_actions(state))


def test_generic_port_gives_three_to_one():
    board = make_board()
    state = make_main_state(board=board)
    put_settlement(state, 0, _port_vertex(board, PortType.GENERIC))
    give(state, 0, brick=3)
    assert state.trade_ratio(0, Resource.BRICK) == 3
    apply_action(state, Action(ActionType.TRADE_BANK, 0, give=Resource.BRICK, get=Resource.WHEAT))
    assert state.players[0].resources[Resource.BRICK] == 0
    assert state.players[0].resources[Resource.WHEAT] == 1


def test_resource_port_gives_two_to_one_for_that_resource_only():
    board = make_board()
    state = make_main_state(board=board)
    put_settlement(state, 0, _port_vertex(board, PortType.WOOD))
    assert state.trade_ratio(0, Resource.WOOD) == 2
    assert state.trade_ratio(0, Resource.ORE) == 4  # no generic port owned

    give(state, 0, wood=2)
    trades = [a for a in legal_actions(state) if a.type is ActionType.TRADE_BANK]
    assert trades and all(a.give is Resource.WOOD for a in trades)


def test_port_requires_building_on_port_vertex():
    board = make_board()
    state = make_main_state(board=board)
    assert state.trade_ratio(0, Resource.WOOD) == 4
    # Opponent building on the port does not grant us the ratio.
    put_settlement(state, 1, _port_vertex(board, PortType.WOOD))
    assert state.trade_ratio(0, Resource.WOOD) == 4
    assert state.trade_ratio(1, Resource.WOOD) == 2


def test_no_domestic_trade_exists():
    # The action space itself has no player-to-player trade (rules.md §10.1).
    assert not any("PLAYER" in t.name and "TRADE" in t.name for t in ActionType)
    state = make_main_state()
    give(state, 0, wood=4, brick=4, sheep=4, wheat=4, ore=4)
    trades = {a.type for a in legal_actions(state) if "TRADE" in a.type.name}
    assert trades == {ActionType.TRADE_BANK}
