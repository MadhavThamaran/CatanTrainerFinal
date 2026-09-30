"""The placement lab (PLACEMENT_LAB_SPEC): a full drill grades exactly the
4 human setup decisions, facts arithmetic is exact, bias-analytics
statements match hand-computed deltas and respect the n>=15 gate, and
attempts survive a restart (append-only JSONL, reloaded fresh)."""
from __future__ import annotations

import json

from engine import Building, TOPOLOGY, new_game

from trainer.lab import (
    LabService, _expansion_spots, _settlement_facts, stats_for,
)
import trainer.lab as lab_module

from helpers import make_board

_PIP = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}


def _svc():
    # net_path=None + tiny sims/dets: fast, no torch, for tests that only
    # need a well-formed (not necessarily strong) grading pass.
    return LabService(sims=4, dets=1, net_path=None)


def _play_full_drill(svc, seed=400_001, user_id=None):
    view = svc.new_drill(user_id=user_id, seed=seed)
    results = []
    steps = 0
    while steps < 10:
        mv = view["moves"][0]
        res = svc.act(view["drill"], mv["codec_id"])
        results.append(res)
        if res["summary"] is not None:
            return results
        view = res["next"]
        steps += 1
    raise AssertionError("drill did not terminate within 10 human decisions")


# --- full drill ---


def test_full_drill_grades_exactly_four_human_decisions():
    svc = _svc()
    results = _play_full_drill(svc)
    assert len(results) == 4
    kinds = [r["grade"]["kind"] for r in results]
    assert kinds == ["settlement", "road", "settlement", "road"]
    for r in results:
        g = r["grade"]
        assert g["regret"] >= 0.0
        assert 0.0 <= g["pct"] <= 1.0
        assert g["verdict"] in (
            "best", "great", "good", "inaccuracy", "mistake", "blunder"
        )
        assert g["chosen_codec_id"] is not None
        assert g["best_codec_id"] is not None
    assert results[-1]["next"] is None
    summary = results[-1]["summary"]
    assert len(summary["grades"]) == 4
    assert summary["total_regret"] >= 0.0


def test_drill_alternates_seat_per_user():
    svc = _svc()
    seats = []
    for i in range(3):
        v = svc.new_drill(user_id=7, seed=400_100 + i)
        seats.append(v["seat"])
    # 1 - last_seat.get(uid, 1) flips 0/1/0 starting from a fresh service
    assert seats == [0, 1, 0]


def test_illegal_codec_is_rejected_softly():
    svc = _svc()
    view = svc.new_drill(user_id=None, seed=400_002)
    legal = {m["codec_id"] for m in view["moves"]}
    bad = next(i for i in range(200) if i not in legal)
    res = svc.act(view["drill"], bad)
    assert res == {"illegal": True}


# --- facts arithmetic (golden) ---


def test_settlement_facts_match_hand_computed_values():
    board = make_board()
    v = next(v for v in range(TOPOLOGY.num_vertices) if len(TOPOLOGY.vertex_hexes[v]) == 3)
    # Hand-compute pips/diversity/ore-share directly from board.numbers/terrain
    # (independent of the _resource_pips helper _settlement_facts itself uses).
    expected: dict = {}
    for h in TOPOLOGY.vertex_hexes[v]:
        n = board.numbers[h]
        res = board.terrain[h].resource
        if n is not None and res is not None:
            expected[res] = expected.get(res, 0) + _PIP[n]
    total = sum(expected.values())

    state = new_game(1, board=board)
    facts = _settlement_facts(state, v)
    assert facts["pips"] == total
    assert facts["div"] == len(expected)
    from engine import Resource

    assert facts["ore_share"] == (
        round(expected.get(Resource.ORE, 0) / total, 3) if total else 0.0
    )
    assert facts["spots"] >= 0


def test_settlement_facts_port_label_exact():
    from engine import Board, PortType, Terrain

    terrain = [Terrain.DESERT] * TOPOLOGY.num_hexes
    numbers: list = [None] * TOPOLOGY.num_hexes
    port_edge = TOPOLOGY.port_slot_edges[0]
    ports = {port_edge: PortType.WHEAT}
    board = Board(terrain=terrain, numbers=numbers, ports=ports)
    v = TOPOLOGY.edge_vertices[port_edge][0]
    hexes = TOPOLOGY.vertex_hexes[v]
    board.terrain[hexes[0]] = Terrain.FIELDS   # wheat
    board.numbers[hexes[0]] = 6                # 5 pips

    state = new_game(1, board=board)
    facts = _settlement_facts(state, v)
    assert facts["pips"] == 5
    assert facts["div"] == 1
    assert facts["port"] == "2:1 wheat"


def test_expansion_spots_shrinks_when_neighbors_are_built():
    board = make_board()
    state = new_game(1, board=board)
    v = 20
    before = _expansion_spots(state, v)
    # Occupy one of v's two-hop neighbors -> spots can only drop or stay.
    two_hop = set()
    for n1 in TOPOLOGY.vertex_neighbors[v]:
        two_hop.update(TOPOLOGY.vertex_neighbors[n1])
    two_hop.discard(v)
    target = next(iter(two_hop))
    state.buildings[target] = (0, Building.SETTLEMENT)
    after = _expansion_spots(state, v)
    assert after <= before


# --- bias analytics ---


def _write_attempt(path, user_id, slot, regret, f_chosen, f_best, kind="settlement"):
    with open(path, "a") as f:
        f.write(json.dumps({
            "user_id": user_id, "drill": "d1", "seed": 1, "seat": 0,
            "slot": slot, "kind": kind, "chosen": 1, "best": 2,
            "regret": regret, "pct": 0.5,
            "f_chosen": f_chosen, "f_best": f_best, "ts": "now",
        }) + "\n")


def test_stats_below_min_n_emits_no_statements(tmp_path, monkeypatch):
    path = tmp_path / "attempts.jsonl"
    monkeypatch.setattr(lab_module, "_ATTEMPTS_PATH", path)
    for i in range(5):   # well below MIN_N=15
        _write_attempt(
            path, 1, "A1", regret=0.1,
            f_chosen={"pips": 5, "div": 2, "port": None, "ore_share": 0.9, "spots": 0},
            f_best={"pips": 12, "div": 4, "port": "2:1 wheat", "ore_share": 0.1, "spots": 3},
        )
    out = stats_for(1)
    assert out["n"] == 5
    assert out["statements"] == [{"kind": "none", "statement": "No clear biases yet — keep drilling."}]


def test_stats_deltas_and_statements_match_hand_computed(tmp_path, monkeypatch):
    path = tmp_path / "attempts.jsonl"
    monkeypatch.setattr(lab_module, "_ATTEMPTS_PATH", path)
    # 15 identical attempts: chosen is ore-heavy, low-diversity, tight, and
    # under-produces relative to best -> every threshold statement should fire.
    for i in range(15):
        _write_attempt(
            path, 1, "A1" if i % 2 == 0 else "B1", regret=0.05,
            f_chosen={"pips": 5, "div": 1, "port": None, "ore_share": 1.0, "spots": 0},
            f_best={"pips": 12, "div": 4, "port": "2:1 wheat", "ore_share": 0.1, "spots": 3},
        )
    out = stats_for(1)
    assert out["n"] == 15
    d = out["deltas"]
    assert d["pips"] == round((5 - 12), 4)
    assert d["diversity"] == round((1 - 4), 4)
    assert d["ore_share"] == round((1.0 - 0.1), 4)
    assert d["spots"] == round((0 - 3), 4)
    kinds = {s["kind"] for s in out["statements"]}
    # max 3 shown even though 4 thresholds trigger
    assert len(out["statements"]) == 3
    assert kinds.issubset({"ore_share", "diversity", "spots", "pips"})
    assert out["by_slot"]["A1"]["n"] == 8
    assert out["by_slot"]["B1"]["n"] == 7
    assert out["by_slot"]["A2"] == {"n": 0, "mean_regret": None}


def test_second_pick_leak_statement_fires_on_a_real_gap(tmp_path, monkeypatch):
    path = tmp_path / "attempts.jsonl"
    monkeypatch.setattr(lab_module, "_ATTEMPTS_PATH", path)
    flat = {"pips": 10, "div": 3, "port": None, "ore_share": 0.3, "spots": 2}
    for _ in range(15):
        _write_attempt(path, 1, "A1", regret=0.02, f_chosen=flat, f_best=flat)
    for _ in range(15):
        _write_attempt(path, 1, "A2", regret=0.10, f_chosen=flat, f_best=flat)
    out = stats_for(1)
    kinds = {s["kind"] for s in out["statements"]}
    assert "second_pick" in kinds


def test_stats_only_sees_the_requesting_users_attempts(tmp_path, monkeypatch):
    path = tmp_path / "attempts.jsonl"
    monkeypatch.setattr(lab_module, "_ATTEMPTS_PATH", path)
    flat = {"pips": 10, "div": 3, "port": None, "ore_share": 0.3, "spots": 2}
    for _ in range(20):
        _write_attempt(path, 999, "A1", regret=0.5, f_chosen=flat, f_best=flat)
    out = stats_for(1)
    assert out["n"] == 0


# --- attempts survive a restart ---


def test_attempts_survive_restart(tmp_path, monkeypatch):
    path = tmp_path / "attempts.jsonl"
    monkeypatch.setattr(lab_module, "_ATTEMPTS_PATH", path)
    svc = _svc()
    _play_full_drill(svc, seed=400_003, user_id=42)
    assert path.exists()
    lines = path.read_text().strip().splitlines()
    # 2 settlement + 2 road attempts logged, all for this user.
    assert len(lines) == 4
    entries = [json.loads(l) for l in lines]
    assert all(e["user_id"] == 42 for e in entries)
    assert sorted(e["kind"] for e in entries) == ["road", "road", "settlement", "settlement"]

    # "restart": a fresh read from disk sees the same data.
    reloaded = stats_for(42)
    assert reloaded["n"] == 2   # settlement-kind attempts only
