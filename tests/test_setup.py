"""Setup phase: A-B-B-A snake order, distance rule, setup road incidence,
starting resources from the second settlement only (rules.md §5)."""
from engine import ActionType, Phase, TOPOLOGY, Terrain, apply_action, legal_actions, new_game
from helpers import make_board


def _do_placement(state, taken_vertices):
    actions = legal_actions(state)
    assert {a.type for a in actions} == {ActionType.SETUP_PLACE_SETTLEMENT}
    # Distance rule: no offered vertex is on or adjacent to an existing settlement.
    for a in actions:
        assert a.vertex not in taken_vertices
        assert not set(TOPOLOGY.vertex_neighbors[a.vertex]) & taken_vertices
    settle = actions[0]
    apply_action(state, settle)

    road_actions = legal_actions(state)
    assert {a.type for a in road_actions} == {ActionType.SETUP_PLACE_ROAD}
    # Setup road must be incident to the just-placed settlement (rules.md §5.3).
    for a in road_actions:
        assert settle.vertex in TOPOLOGY.edge_vertices[a.edge]
    apply_action(state, road_actions[0])
    return settle.vertex, settle.player


def test_snake_order_distance_rule_and_starting_resources():
    board = make_board()
    state = new_game(0, board=board)
    assert state.phase is Phase.SETUP

    taken: set[int] = set()
    placements = []
    for _ in range(4):
        actor = state.player_to_act()
        vertex, player = _do_placement(state, taken)
        assert player == actor
        placements.append((player, vertex))
        taken.add(vertex)

    # Snake draft A -> B -> B -> A (rules.md §5.1).
    assert [p for p, _ in placements] == [0, 1, 1, 0]

    # Starting resources come only from each player's *second* settlement
    # (rules.md §5.4), i.e. placements 2 (B) and 3 (A).
    for player, second_vertex in ((1, placements[2][1]), (0, placements[3][1])):
        expected = sum(
            1
            for h in TOPOLOGY.vertex_hexes[second_vertex]
            if board.terrain[h] is not Terrain.DESERT
        )
        assert state.players[player].hand_size() == expected

    assert state.phase is Phase.MAIN
    assert state.current_player == 0
    assert state.needs_roll


def test_setup_consumes_pieces():
    state = new_game(1, board=make_board())
    for _ in range(8):
        apply_action(state, legal_actions(state)[0])
    for p in (0, 1):
        assert state.players[p].settlements_left == 3
        assert state.players[p].roads_left == 13
