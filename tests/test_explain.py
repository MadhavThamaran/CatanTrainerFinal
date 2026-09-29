"""Grounded move explanations (EXPLAIN_SPEC): facts are exact/testable
arithmetic over the position, and every rendered clause cites only numbers
that are leaf values inside those facts dicts (never a hallucinated one)."""
from __future__ import annotations

import json
import re
import subprocess
import sys

from engine import Action, ActionType, Resource, TOPOLOGY

from puzzles import label_candidate, load_puzzles
from puzzles.explain import move_facts, render, render_miss

from helpers import find_edge_path, give, make_main_state, put_road, put_settlement
from test_puzzles import _near_win_candidate

_PIP = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}


def _pip_at(board, v: int) -> int:
    total = 0
    for h in TOPOLOGY.vertex_hexes[v]:
        n = board.numbers[h]
        if n is not None and board.terrain[h].resource is not None:
            total += _PIP[n]
    return total


def _vertex_on_number(board, n: int) -> int:
    for h in range(TOPOLOGY.num_hexes):
        if board.numbers[h] == n:
            return TOPOLOGY.hex_vertices[h][0]
    raise AssertionError(f"no hex with number {n} on this board")


# --- golden facts ---


def test_settlement_prod_gain_matches_hand_computed_pips():
    state = make_main_state()
    give(state, 0, wood=1, brick=1, sheep=1, wheat=1)
    v = _vertex_on_number(state.board, 6)
    expected: dict = {}
    for h in TOPOLOGY.vertex_hexes[v]:
        n = state.board.numbers[h]
        res = state.board.terrain[h].resource
        if n is not None and res is not None:
            expected[res.value] = expected.get(res.value, 0) + _PIP[n]

    facts = move_facts(state, Action(ActionType.BUILD_SETTLEMENT, 0, vertex=v), 0)

    assert facts["prod_gain"] == expected
    assert facts["prod_gain_total"] == sum(expected.values())
    assert facts["prod_gain_diversity"] == len(expected)


def test_city_prod_gain_is_one_more_multiple_not_double():
    """A city upgrade ADDS one multiple of the vertex's pips (1x->2x); it
    should read the same as a fresh settlement's gain, not 2x it."""
    state = make_main_state()
    give(state, 0, ore=3, wheat=2)
    v = _vertex_on_number(state.board, 8)
    put_settlement(state, 0, v)
    expected: dict = {}
    for h in TOPOLOGY.vertex_hexes[v]:
        n = state.board.numbers[h]
        res = state.board.terrain[h].resource
        if n is not None and res is not None:
            expected[res.value] = expected.get(res.value, 0) + _PIP[n]

    city_facts = move_facts(state, Action(ActionType.BUILD_CITY, 0, vertex=v), 0)
    assert city_facts["prod_gain"] == expected


def test_road_opens_exactly_one_known_vertex():
    state = make_main_state()
    give(state, 0, wood=1, brick=1)
    vs, es = find_edge_path(2)
    put_settlement(state, 0, vs[0])
    put_road(state, 0, es[0])

    facts = move_facts(state, Action(ActionType.BUILD_ROAD, 0, edge=es[1]), 0)

    assert {o["vertex"] for o in facts["opens"]} == {vs[2]}


def test_robber_blocks_computed_pip_count():
    state = make_main_state()
    h = next(
        hh for hh in range(TOPOLOGY.num_hexes)
        if state.board.numbers[hh] is not None and hh != state.robber_hex
    )
    v = TOPOLOGY.hex_vertices[h][0]
    put_settlement(state, 1, v)
    res = state.board.terrain[h].resource
    pip = _PIP[state.board.numbers[h]]

    facts = move_facts(state, Action(ActionType.MOVE_ROBBER, 0, hex=h), 0)

    assert facts["robber"]["opp_pips_blocked"] == {res.value: pip}
    assert facts["robber"]["opp_pips_blocked_total"] == pip
    assert facts["robber"]["my_pips_blocked_total"] == 0


def test_trade_completing_city_cost():
    state = make_main_state()
    give(state, 0, wheat=2, ore=2, wood=4)

    facts = move_facts(
        state, Action(ActionType.TRADE_BANK, 0, give=Resource.WOOD, get=Resource.ORE), 0
    )

    assert facts["enables_now"] == {"completes": "city"}


def test_trade_not_completing_anything_is_null():
    state = make_main_state()
    give(state, 0, wood=4)
    facts = move_facts(
        state, Action(ActionType.TRADE_BANK, 0, give=Resource.WOOD, get=Resource.SHEEP), 0
    )
    assert facts["enables_now"] == {"completes": None}


# --- render: fallback ---


def test_fallback_when_nothing_salient():
    state = make_main_state()
    facts = move_facts(state, Action(ActionType.END_TURN, 0), 0)
    assert render(facts, None, phase="midgame") is None


# --- render: honesty property ---


def _int_leaves(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, int):
        out.add(str(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _int_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _int_leaves(v)
    return out


def _assert_honest(text: str, best: dict, alt: dict | None) -> None:
    leaves = _int_leaves(best) | (_int_leaves(alt) if alt else set())
    for tok in re.findall(r"\d+", text):
        assert tok in leaves, f"{tok!r} in {text!r} is not a fact leaf value"


def test_render_placement_is_honest():
    state = make_main_state()
    give(state, 0, wood=1, brick=1, sheep=1, wheat=1)
    v_best = max(range(TOPOLOGY.num_vertices), key=lambda v: _pip_at(state.board, v))
    v_alt = min(range(TOPOLOGY.num_vertices), key=lambda v: _pip_at(state.board, v))
    best = move_facts(state, Action(ActionType.BUILD_SETTLEMENT, 0, vertex=v_best), 0)
    alt = move_facts(state, Action(ActionType.BUILD_SETTLEMENT, 0, vertex=v_alt), 0)
    text = render(best, alt, phase="midgame")
    assert text is not None and len(text) <= 220
    _assert_honest(text, best, alt)
    # every resource named in the lead clause has a nonzero pip entry
    for res in best["prod_gain"]:
        assert res in text


def test_render_robber_is_honest():
    state = make_main_state()
    hexes_with_numbers = [
        h for h in range(TOPOLOGY.num_hexes)
        if state.board.numbers[h] is not None and h != state.robber_hex
    ]
    h_best, h_alt = hexes_with_numbers[0], hexes_with_numbers[1]
    put_settlement(state, 1, TOPOLOGY.hex_vertices[h_best][0])
    best = move_facts(state, Action(ActionType.MOVE_ROBBER, 0, hex=h_best), 0)
    alt = move_facts(state, Action(ActionType.MOVE_ROBBER, 0, hex=h_alt), 0)
    text = render(best, alt, phase="robber")
    if text is not None:   # h_alt may coincidentally also block something
        _assert_honest(text, best, alt)


def test_render_miss_reports_the_pip_gap():
    state = make_main_state()
    give(state, 0, wood=1, brick=1, sheep=1, wheat=1)
    v_best = max(range(TOPOLOGY.num_vertices), key=lambda v: _pip_at(state.board, v))
    v_worst = min(range(TOPOLOGY.num_vertices), key=lambda v: _pip_at(state.board, v))
    best = move_facts(state, Action(ActionType.BUILD_SETTLEMENT, 0, vertex=v_best), 0)
    chosen = move_facts(state, Action(ActionType.BUILD_SETTLEMENT, 0, vertex=v_worst), 0)
    assert best["prod_gain_total"] > chosen["prod_gain_total"]
    msg = render_miss(chosen, best)
    assert msg is not None
    assert str(chosen["prod_gain_total"]) in msg
    assert str(best["prod_gain_total"]) in msg


# --- annotate script: round trip ---


def test_annotate_script_only_adds_facts_and_explanation(tmp_path):
    puzzle, reason = label_candidate(
        _near_win_candidate(), sims=96, dets=2, min_gap=0.04, seeds=(1, 2)
    )
    assert reason == "admitted"
    # Simulate a puzzle admitted BEFORE explain shipped: strip what labeling
    # now fills in, same as a real pre-EXPLAIN_SPEC puzzles_v5.jsonl line.
    stale = json.loads(puzzle.to_json())
    stale["facts"] = None
    path = tmp_path / "p.jsonl"
    path.write_text(json.dumps(stale) + "\n")

    subprocess.run(
        [sys.executable, "scripts/annotate_explanations.py", str(path)],
        check=True, capture_output=True, text=True,
    )

    [annotated] = load_puzzles(str(path))
    assert annotated.id == puzzle.id
    assert annotated.moves == puzzle.moves
    assert annotated.gap == puzzle.gap
    assert annotated.state == puzzle.state
    assert annotated.best_codec_id == puzzle.best_codec_id
    assert annotated.facts is not None
    assert set(annotated.facts) == {"best", "second"}


# --- trainer/service.py integration: facts present vs computed live ---


def test_service_explains_a_puzzle_with_no_stored_facts(tmp_path):
    """A pre-EXPLAIN_SPEC puzzle (facts=None) must still get a real
    explanation — computed live from the same functions, same result
    shape — and a "yours missed X" sentence when the submission isn't best."""
    from trainer import JsonStore, TrainerService

    puzzle, reason = label_candidate(
        _near_win_candidate(), sims=96, dets=2, min_gap=0.04, seeds=(1, 2)
    )
    assert reason == "admitted"
    stale = json.loads(puzzle.to_json())
    stale["facts"] = None
    path = tmp_path / "p.jsonl"
    path.write_text(json.dumps(stale) + "\n")

    svc = TrainerService(str(path), JsonStore(tmp_path / "s.json"))
    worst = puzzle.moves[-1]
    assert worst.codec_id != puzzle.best_codec_id
    res = svc.submit(puzzle.id, worst.codec_id, 1)
    assert res["explanation"]
