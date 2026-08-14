"""Discard on a 7: only hands of MORE than 9 cards, discard half rounded
down (rules.md §2.2, §6.3)."""
from engine import Action, ActionType, ScriptedDice, apply_action, legal_actions
from helpers import give, make_main_state


def _roll_seven(state):
    state.needs_roll = True
    apply_action(state, Action(ActionType.ROLL, state.current_player))


def test_hand_of_nine_does_not_discard():
    state = make_main_state(dice=ScriptedDice([7]))
    give(state, 0, wood=9)
    _roll_seven(state)
    assert state.pending_discards == []
    assert state.pending_robber


def test_hand_of_ten_discards_five():
    state = make_main_state(dice=ScriptedDice([7]))
    give(state, 1, wood=4, brick=3, ore=3)  # 10 cards
    _roll_seven(state)
    assert state.pending_discards == [1]
    assert state.player_to_act() == 1  # discarder acts before the robber moves

    discards = legal_actions(state)
    assert {a.type for a in discards} == {ActionType.DISCARD}
    assert all(len(a.resources) == 5 for a in discards)  # floor(10 / 2)

    apply_action(state, discards[0])
    assert state.players[1].hand_size() == 5
    assert state.pending_discards == []
    assert state.pending_robber  # robber move comes after discards


def test_odd_hand_discards_floor():
    state = make_main_state(dice=ScriptedDice([7]))
    give(state, 0, sheep=11)
    _roll_seven(state)
    [action] = [a for a in legal_actions(state) if a.type is ActionType.DISCARD][:1]
    assert len(action.resources) == 5  # floor(11 / 2)
    apply_action(state, action)
    assert state.players[0].hand_size() == 6


def test_both_players_over_limit_both_discard():
    state = make_main_state(dice=ScriptedDice([7]))
    give(state, 0, wood=10)
    give(state, 1, ore=12)
    _roll_seven(state)
    assert state.pending_discards == [0, 1]
    apply_action(state, legal_actions(state)[0])  # player 0 discards
    assert state.pending_discards == [1]
    apply_action(state, legal_actions(state)[0])  # player 1 discards
    assert state.players[0].hand_size() == 5
    assert state.players[1].hand_size() == 6
