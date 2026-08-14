"""State views (rules.md §14): clone independence, observation hiding."""
from engine import DevCard, Resource, apply_action, legal_actions, new_game
from helpers import give, make_main_state


def test_clone_is_independent():
    state = make_main_state()
    give(state, 0, wood=2)
    twin = state.clone()
    give(twin, 0, wood=5)
    twin.buildings[0] = state.buildings.get(0, (1, None))
    twin.dev_deck.pop()
    assert state.players[0].resources[Resource.WOOD] == 2
    assert 0 not in state.buildings
    assert len(state.dev_deck) == len(twin.dev_deck) + 1


def test_clone_replays_identically():
    state = new_game(3)
    twin = state.clone()
    for _ in range(30):
        actions = legal_actions(state)
        twin_actions = legal_actions(twin)
        assert actions == twin_actions
        apply_action(state, actions[0])
        apply_action(twin, twin_actions[0])
    assert state.to_dict() == twin.to_dict()


def test_observation_hides_hidden_information():
    state = make_main_state()
    give(state, 1, wood=3, ore=2)
    state.players[1].dev_cards[DevCard.VICTORY_POINT] = 2

    obs = state.observation(0)
    assert "dev_deck" not in obs                      # deck order hidden
    assert obs["dev_deck_count"] == len(state.dev_deck)
    opp = obs["players"]["opponent"]
    assert opp["hand_size"] == 5                      # count only
    assert "resources" not in opp                     # composition hidden
    assert opp["dev_card_count"] == 2                 # count only
    assert "dev_cards" not in opp
    assert opp["visible_vp"] == 0                     # hidden VP not exposed

    own = obs["players"]["own"]
    assert own["resources"][Resource.WOOD.value] == 0
    assert own["dev_cards"][DevCard.VICTORY_POINT.value] == 0


def test_perfect_state_serializes():
    state = make_main_state()
    d = state.to_dict()
    assert d["phase"] == "MAIN"
    assert len(d["players"]) == 2
    assert len(d["dev_deck"]) == 25
