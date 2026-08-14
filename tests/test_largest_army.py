"""Largest army (rules.md §12): >= 3 knights, tie -> holder retains,
strictly more knights transfers the award."""
from engine import Action, ActionType, DevCard, ScriptedDice, apply_action, legal_actions
from engine.rules import _update_largest_army
from helpers import make_main_state


def test_three_knights_earn_largest_army_through_real_turns():
    state = make_main_state(dice=ScriptedDice([2]))
    state.players[0].dev_cards[DevCard.KNIGHT] = 3
    state.needs_roll = True

    for turn in range(3):
        apply_action(state, Action(ActionType.ROLL, 0))
        assert Action(ActionType.PLAY_KNIGHT, 0) in legal_actions(state)
        apply_action(state, Action(ActionType.PLAY_KNIGHT, 0))
        # Knight forces a robber move (rules.md §8.1).
        move = next(a for a in legal_actions(state) if a.type is ActionType.MOVE_ROBBER)
        apply_action(state, move)
        # One dev card per turn (rules.md §8.6).
        assert not any(a.type is ActionType.PLAY_KNIGHT for a in legal_actions(state))
        apply_action(state, Action(ActionType.END_TURN, 0))
        apply_action(state, Action(ActionType.ROLL, 1))
        apply_action(state, Action(ActionType.END_TURN, 1))

        if turn < 2:
            assert state.largest_army_holder is None

    assert state.players[0].knights_played == 3
    assert state.largest_army_holder == 0
    assert state.visible_vp(0) == 2  # LA bonus only


def test_tie_retains_and_strictly_more_transfers():
    state = make_main_state()
    state.players[0].knights_played = 3
    _update_largest_army(state, 0)
    assert state.largest_army_holder == 0

    state.players[1].knights_played = 3
    _update_largest_army(state, 1)
    assert state.largest_army_holder == 0  # tie -> holder retains

    state.players[1].knights_played = 4
    _update_largest_army(state, 1)
    assert state.largest_army_holder == 1  # strictly more -> transfer
    assert state.visible_vp(1) == 2
    assert state.visible_vp(0) == 0
