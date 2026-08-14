"""Dice production: settlement = 1, city = 2, robber blocks (rules.md §6.2, §9.1)."""
from engine import ActionType, Action, ScriptedDice, TOPOLOGY, apply_action
from helpers import make_board, make_main_state, put_city, put_settlement


def _pick_production_hex(board):
    """A non-desert hex whose number is unique on the board, so expected
    payouts are unambiguous."""
    for h, n in enumerate(board.numbers):
        if n is not None and board.numbers.count(n) == 1:
            return h, n
    raise AssertionError("standard boards always have doubled-up numbers missing")


def test_settlement_and_city_production():
    board = make_board()
    hexid, number = _pick_production_hex(board)
    resource = board.terrain[hexid].resource
    v_settle, v_city = TOPOLOGY.hex_vertices[hexid][0], TOPOLOGY.hex_vertices[hexid][2]

    state = make_main_state(board=board, dice=ScriptedDice([number]))
    put_settlement(state, 0, v_settle)
    put_city(state, 1, v_city)
    state.needs_roll = True
    assert state.robber_hex != hexid  # robber starts on the desert

    apply_action(state, Action(ActionType.ROLL, 0))
    assert state.last_roll == number
    assert state.players[0].resources[resource] == 1  # settlement -> 1
    assert state.players[1].resources[resource] == 2  # city -> 2


def test_robber_blocks_production():
    board = make_board()
    hexid, number = _pick_production_hex(board)
    state = make_main_state(board=board, dice=ScriptedDice([number]))
    put_settlement(state, 0, TOPOLOGY.hex_vertices[hexid][0])
    state.robber_hex = hexid
    state.needs_roll = True

    apply_action(state, Action(ActionType.ROLL, 0))
    assert state.players[0].hand_size() == 0
