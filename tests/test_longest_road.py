"""Longest road (rules.md §11): >= 5 to claim, opponent settlements break
paths, tie -> holder retains / no holder -> nobody."""
from engine import (
    Action,
    ActionType,
    TOPOLOGY,
    apply_action,
    longest_road_length,
)
from engine.rules import _update_longest_road
from helpers import find_edge_path, give, make_main_state, put_road, put_settlement


def test_path_length_and_minimum_of_five():
    state = make_main_state()
    _, edges = find_edge_path(4)
    for e in edges:
        put_road(state, 0, e)
    assert longest_road_length(state, 0) == 4
    _update_longest_road(state)
    assert state.longest_road_holder is None  # 4 < 5

    _, edges5 = find_edge_path(5)
    state2 = make_main_state()
    for e in edges5:
        put_road(state2, 0, e)
    _update_longest_road(state2)
    assert state2.longest_road_holder == 0
    assert state2.visible_vp(0) == 2  # LR bonus


def test_own_settlement_does_not_break_road():
    state = make_main_state()
    verts, edges = find_edge_path(5)
    for e in edges:
        put_road(state, 0, e)
    put_settlement(state, 0, verts[2])  # own building mid-path
    assert longest_road_length(state, 0) == 5


def test_opponent_settlement_cuts_road_and_removes_award():
    state = make_main_state()
    verts, edges = find_edge_path(6)
    for e in edges:
        put_road(state, 0, e)
    _update_longest_road(state)
    assert state.longest_road_holder == 0

    # Find a mid-path vertex with a spare (non-path) incident edge for the
    # opponent to connect through, then have them build a settlement there
    # via the normal rules so the recompute triggers.
    cut_vertex = spare_edge = None
    for i in range(1, 6):
        spares = [e for e in TOPOLOGY.vertex_edges[verts[i]] if e not in edges]
        if spares:
            cut_vertex, spare_edge = verts[i], spares[0]
            cut_pos = i
            break
    assert cut_vertex is not None
    put_road(state, 1, spare_edge)
    give(state, 1, wood=1, brick=1, sheep=1, wheat=1)
    state.current_player = 1
    apply_action(state, Action(ActionType.BUILD_SETTLEMENT, 1, vertex=cut_vertex))

    assert longest_road_length(state, 0) == max(cut_pos, 6 - cut_pos)
    assert state.longest_road_holder is None  # cut below 5, nobody qualifies


def test_tie_holder_retains_and_fresh_tie_awards_nobody():
    state = make_main_state()
    verts0, edges0 = find_edge_path(5)
    forbidden = frozenset(verts0)
    _, edges1 = find_edge_path(5, forbidden_vertices=forbidden)
    for e in edges0:
        put_road(state, 0, e)
    _update_longest_road(state)
    assert state.longest_road_holder == 0

    for e in edges1:
        put_road(state, 1, e)
    _update_longest_road(state)
    assert state.longest_road_holder == 0  # tie -> current holder retains

    # Same two 5-roads but no incumbent: tie -> nobody gets it.
    state.longest_road_holder = None
    _update_longest_road(state)
    assert state.longest_road_holder is None
