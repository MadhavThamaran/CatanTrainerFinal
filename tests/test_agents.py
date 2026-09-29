"""Stage 3 validation (PLAN.md): placement ranking sanity, heuristic >>
random, and greedy self-play terminating in a real 15-VP win."""
import random

import pytest

from agents import (
    GreedyRandomAgent,
    HeuristicAgent,
    RandomAgent,
    evaluate,
    placement_value,
    play_game,
)
from agents.heuristic import production_value
from engine import Action, ActionType, Board, PortType, TOPOLOGY, Terrain
from helpers import make_main_state, put_settlement


def _blank_board() -> Board:
    """Empty board (all desert / no numbers) with generic ports on every
    slot — a canvas for placing specific hexes under test vertices."""
    terrain = [Terrain.DESERT] * TOPOLOGY.num_hexes
    numbers: list[int | None] = [None] * TOPOLOGY.num_hexes
    ports = {e: PortType.GENERIC for e in TOPOLOGY.port_slot_edges}
    return Board(terrain=terrain, numbers=numbers, ports=ports)


def _set_hex(board, h, terrain, number):
    board.terrain[h] = terrain
    board.numbers[h] = number


def _interior_vertex() -> int:
    return next(v for v in range(TOPOLOGY.num_vertices) if len(TOPOLOGY.vertex_hexes[v]) == 3)


def test_high_pip_diverse_spot_outranks_weak_spot():
    board = _blank_board()
    strong = _interior_vertex()
    hexes = TOPOLOGY.vertex_hexes[strong]
    _set_hex(board, hexes[0], Terrain.FIELDS, 6)      # wheat, 5 pips
    _set_hex(board, hexes[1], Terrain.MOUNTAINS, 8)   # ore, 5 pips
    _set_hex(board, hexes[2], Terrain.FOREST, 5)      # wood, 4 pips

    # A weak spot: touches the desert and a 2 (1 pip).
    weak = next(
        v
        for v in range(TOPOLOGY.num_vertices)
        if v != strong and set(TOPOLOGY.vertex_hexes[v]).isdisjoint(hexes)
    )
    for h in TOPOLOGY.vertex_hexes[weak]:
        _set_hex(board, h, Terrain.DESERT, None)
    _set_hex(board, TOPOLOGY.vertex_hexes[weak][0], Terrain.PASTURE, 2)  # sheep, 1 pip

    assert placement_value(board, strong) > placement_value(board, weak)


def test_six_eight_spot_beats_two_twelve_spot_same_resources():
    board_hi = _blank_board()
    board_lo = _blank_board()
    v = _interior_vertex()
    hexes = TOPOLOGY.vertex_hexes[v]
    for b, (n1, n2) in ((board_hi, (6, 8)), (board_lo, (2, 12))):
        _set_hex(b, hexes[0], Terrain.FIELDS, n1)
        _set_hex(b, hexes[1], Terrain.MOUNTAINS, n2)
    assert placement_value(board_hi, v) > placement_value(board_lo, v)


def test_generic_port_adds_value():
    board = _blank_board()
    v = _interior_vertex()
    _set_hex(board, TOPOLOGY.vertex_hexes[v][0], Terrain.FIELDS, 6)
    base = placement_value(board, v)
    # Attach a generic port to one of v's incident coastal edges if possible;
    # otherwise assert the port term directly via a known port vertex.
    port_vertex = TOPOLOGY.edge_vertices[TOPOLOGY.port_slot_edges[0]][0]
    _set_hex(board, TOPOLOGY.vertex_hexes[port_vertex][0], Terrain.FIELDS, 6)
    # Same single-hex production, but the port vertex should score higher.
    plain = _blank_board()
    _set_hex(plain, TOPOLOGY.vertex_hexes[port_vertex][0], Terrain.FIELDS, 6)
    # Remove ports from the plain board to isolate the port bonus.
    plain.ports = {}
    assert placement_value(board, port_vertex) > placement_value(plain, port_vertex)
    assert base >= 0  # sanity


def test_production_value_doubles_intuition():
    board = _blank_board()
    v = _interior_vertex()
    _set_hex(board, TOPOLOGY.vertex_hexes[v][0], Terrain.MOUNTAINS, 8)  # 5 pips ore
    # ore weight 1.1 * 5 pips = 5.5
    assert production_value(board, v) == pytest.approx(5.5)


def test_heuristic_sweeps_random():
    # Uniform random should never win: a clean sweep over a real sample.
    report = evaluate(
        agent_a=lambda s: HeuristicAgent(),
        agent_b=lambda s: RandomAgent(s),
        games=40,
    )
    assert report.a_wins == report.games, report
    # Wins come from actually racing to 15 VP, not from cap tiebreaks.
    assert report.a_reached_15 == report.a_wins


def test_heuristic_dominates_greedy_baseline():
    # Against a build-prioritizing opponent (much tougher than uniform
    # random), the heuristic still wins the overwhelming majority; the rare
    # loss is opponent dev-card variance, not a board weakness.
    report = evaluate(
        agent_a=lambda s: HeuristicAgent(),
        agent_b=lambda s: GreedyRandomAgent(s),
        games=40,
    )
    assert report.a_win_rate >= 0.9, report


def test_greedy_self_play_reaches_a_real_win():
    result = play_game(HeuristicAgent(), HeuristicAgent(), seed=3)
    assert result.decided_by == "victory", result
    assert result.winner in (0, 1)
    assert max(result.vps) >= 15


def test_robber_score_finds_buildings_on_the_targeted_hex():
    """Regression: `_robber_score` indexed `TOPOLOGY.vertex_hexes[a.hex]`
    (vertex -> hexes) instead of `TOPOLOGY.hex_vertices[a.hex]` (hex ->
    vertices) — `a.hex` is a hex id, so it was scoring buildings on an
    unrelated, coincidentally-numbered vertex instead of the hex it was
    actually evaluating, completely blind to the real target."""
    board = _blank_board()
    target_hex = 0
    settlement_vertex = TOPOLOGY.hex_vertices[target_hex][0]
    # A hex that shares NO vertex with the settlement (not just a
    # different id) — otherwise a shared corner would legitimately score
    # both hexes and mask the regression.
    touching = set(TOPOLOGY.vertex_hexes[settlement_vertex])
    quiet_hex = next(h for h in range(TOPOLOGY.num_hexes) if h not in touching)
    _set_hex(board, target_hex, Terrain.HILLS, 6)   # brick, 5 pips
    _set_hex(board, quiet_hex, Terrain.HILLS, 6)     # same production, no building

    state = make_main_state(board=board)
    state.robber_hex = quiet_hex
    put_settlement(state, 1, settlement_vertex)
    state.current_player = 0

    agent = HeuristicAgent()
    a_target = Action(ActionType.MOVE_ROBBER, 0, hex=target_hex)
    a_quiet = Action(ActionType.MOVE_ROBBER, 0, hex=quiet_hex)
    score_target, score_quiet = agent.score_actions(state, [a_target, a_quiet])
    assert score_target > score_quiet, (
        "robbing the hex the opponent actually settled must outscore an "
        "equally productive hex with no building on it"
    )


def test_agent_only_returns_legal_actions():
    from engine import legal_actions, apply_action, new_game, Phase

    agent = HeuristicAgent()
    state = new_game(1)
    for _ in range(300):
        if state.phase is Phase.GAME_OVER:
            break
        legal = legal_actions(state)
        chosen = agent.select_action(state)
        assert chosen in legal
        apply_action(state, chosen)
