"""Development cards (rules.md §7.4, §8): buy cost, same-turn play ban,
one non-VP card per turn, each card's effect, hidden VP and the 15-VP win."""
from engine import (
    Action,
    ActionType,
    DevCard,
    Phase,
    Resource,
    ScriptedDice,
    apply_action,
    legal_actions,
)
from helpers import find_edge_path, give, make_main_state, put_city, put_road, put_settlement


def test_buy_cost_and_same_turn_play_ban():
    state = make_main_state(dice=ScriptedDice([2]))
    state.dev_deck = [DevCard.KNIGHT]
    give(state, 0, ore=1, wheat=1, sheep=1)

    apply_action(state, Action(ActionType.BUY_DEV_CARD, 0))
    assert state.players[0].hand_size() == 0  # cost paid
    assert state.players[0].dev_cards[DevCard.KNIGHT] == 1
    assert not state.dev_deck
    # Bought this turn -> not playable this turn (rules.md §7.4).
    assert not any(a.type is ActionType.PLAY_KNIGHT for a in legal_actions(state))

    apply_action(state, Action(ActionType.END_TURN, 0))
    apply_action(state, Action(ActionType.ROLL, 1))
    apply_action(state, Action(ActionType.END_TURN, 1))
    apply_action(state, Action(ActionType.ROLL, 0))
    # Next own turn -> playable.
    assert any(a.type is ActionType.PLAY_KNIGHT for a in legal_actions(state))


def test_one_non_vp_dev_card_per_turn():
    state = make_main_state()
    state.players[0].dev_cards[DevCard.YEAR_OF_PLENTY] = 1
    state.players[0].dev_cards[DevCard.MONOPOLY] = 1
    apply_action(
        state,
        Action(ActionType.PLAY_YEAR_OF_PLENTY, 0, resources=(Resource.ORE, Resource.ORE)),
    )
    assert state.players[0].resources[Resource.ORE] == 2
    types = {a.type for a in legal_actions(state)}
    assert ActionType.PLAY_MONOPOLY not in types
    assert ActionType.PLAY_YEAR_OF_PLENTY not in types


def test_monopoly_takes_all_of_declared_resource():
    state = make_main_state()
    state.players[0].dev_cards[DevCard.MONOPOLY] = 1
    give(state, 1, wheat=4, wood=2)
    apply_action(state, Action(ActionType.PLAY_MONOPOLY, 0, get=Resource.WHEAT))
    assert state.players[0].resources[Resource.WHEAT] == 4
    assert state.players[1].resources[Resource.WHEAT] == 0
    assert state.players[1].resources[Resource.WOOD] == 2  # untouched


def test_road_building_places_two_free_roads():
    state = make_main_state()
    verts, edges = find_edge_path(1)
    put_settlement(state, 0, verts[0])  # network anchor
    state.players[0].dev_cards[DevCard.ROAD_BUILDING] = 1

    apply_action(state, Action(ActionType.PLAY_ROAD_BUILDING, 0))
    assert state.free_roads == 2
    for _ in range(2):
        placements = legal_actions(state)
        assert {a.type for a in placements} == {ActionType.BUILD_ROAD}  # forced
        apply_action(state, placements[0])
    assert state.free_roads == 0
    assert state.players[0].hand_size() == 0  # roads were free
    assert sum(1 for p in state.roads.values() if p == 0) == 2


def test_road_building_requires_a_legal_placement():
    state = make_main_state()
    state.players[0].dev_cards[DevCard.ROAD_BUILDING] = 1
    # No network at all -> no legal road -> card not playable (rules.md §8.2).
    assert not any(
        a.type is ActionType.PLAY_ROAD_BUILDING for a in legal_actions(state)
    )


def test_hidden_vp_win_at_fifteen():
    state = make_main_state()
    # 5 settlements + 4 cities = 13 visible VP.
    for v in (0, 10, 20, 30, 40):
        put_settlement(state, 0, v)
    for v in (45, 47, 50, 52):
        put_city(state, 0, v)
    state.players[0].dev_cards[DevCard.VICTORY_POINT] = 1
    assert state.visible_vp(0) == 13     # VP card excluded from visible
    assert state.total_vp(0) == 14

    state.dev_deck = [DevCard.VICTORY_POINT]
    give(state, 0, ore=1, wheat=1, sheep=1)
    apply_action(state, Action(ActionType.BUY_DEV_CARD, 0))  # draws the 15th VP
    assert state.phase is Phase.GAME_OVER
    assert state.winner == 0
