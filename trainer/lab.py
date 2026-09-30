"""Placement lab (PLACEMENT_LAB_SPEC): a dedicated drill for 1v1 Catan's
opening theory. A drill is one fresh board, one seat (alternated), the
A-B-B-A setup snake; the human makes 2 settlement + 2 road picks, each
graded BEFORE it applies against a deep search pass, while the engine
plays the other seat. Every graded settlement pick is logged to
`data/lab_attempts.jsonl` for the longitudinal bias analytics in §4.

No hidden information exists during setup (no hands, no dev deck draws
visible to either seat), so grading needs no CardTracker/observe() —
`evaluate(state, viewer=actor)` is exact, not merely determinized.
"""
from __future__ import annotations

import json
import random
import threading
from datetime import datetime, timezone
from pathlib import Path

from engine import (
    ActionType, GameState, Phase, PortType, Resource, TOPOLOGY,
    apply_action, legal_actions, new_game,
)
from engine.state import SETUP_ORDER
from net.codec import encode_action
from search import MCTSEngine

from agents.heuristic import _port_type_at, _resource_pips

from .actions import describe_move
from .layout import LAYOUT
from .play import _gained
from .service import board_state, context
from puzzles.explain import move_facts, render
from puzzles.scoring import VERDICT_FOR_POINTS, points_for_regret

LAB_SIMS = 256
LAB_DETS = 4
LAB_NET_PATH = "checkpoints/gen7.pt"
LAB_SEED_BASE = 400_000   # seed ledger: lab reserves 400000+
_LAB_SEED_SPAN = 90_000_000

_MAX_SESSIONS = 8
_SLOT_FOR_INDEX = {0: "A1", 1: "B1", 2: "B2", 3: "A2"}

_ATTEMPTS_PATH = Path("data/lab_attempts.jsonl")
_ATTEMPTS_LOCK = threading.Lock()


# --- compact per-pick facts (EXPLAIN_SPEC's helpers, a lab-specific subset) ---


def _port_label(board, vertex: int) -> str | None:
    port = _port_type_at(board, vertex)
    if port is None:
        return None
    return "3:1" if port is PortType.GENERIC else f"2:1 {port.resource.value}"


def _expansion_spots(state: GameState, vertex: int) -> int:
    """Empty, distance-rule-legal vertices two hops out — "room to grow"
    once roads reach them. Accounts for THIS placement itself blocking
    its own neighbors (the state hasn't been mutated yet)."""
    two_hop: set[int] = set()
    for n1 in TOPOLOGY.vertex_neighbors[vertex]:
        two_hop.update(TOPOLOGY.vertex_neighbors[n1])
    two_hop.discard(vertex)

    def empty_after(v2: int) -> bool:
        if v2 in state.buildings or v2 == vertex:
            return False
        return all(
            nb not in state.buildings and nb != vertex
            for nb in TOPOLOGY.vertex_neighbors[v2]
        )

    return sum(1 for v2 in two_hop if empty_after(v2))


def _settlement_facts(state: GameState, vertex: int) -> dict:
    pips = _resource_pips(state.board, vertex)
    total = sum(pips.values())
    return {
        "pips": total,
        "div": len(pips),
        "port": _port_label(state.board, vertex),
        "ore_share": round(pips.get(Resource.ORE, 0) / total, 3) if total else 0.0,
        "spots": _expansion_spots(state, vertex),
    }


def _road_facts(state: GameState, edge: int, settlement_vertex: int) -> dict:
    far = TOPOLOGY.edge_other_vertex(edge, settlement_vertex)
    return {"points_to": far, "pips": sum(_resource_pips(state.board, far).values())}


def _pick_facts(state: GameState, action, kind: str) -> dict:
    if kind == "settlement":
        return _settlement_facts(state, action.vertex)
    return _road_facts(state, action.edge, state.last_setup_settlement)


def _explain_grade(state: GameState, actor: int, chosen, best, q_best: float, q_chosen: float, kind: str) -> str:
    if kind == "settlement":
        text = render(move_facts(state, best, actor), move_facts(state, chosen, actor), phase="placement")
        if text:
            return text
    best_label = describe_move(encode_action(best), state, actor)["label"]
    chosen_label = describe_move(encode_action(chosen), state, actor)["label"]
    return (
        f"Best: {best_label} — {q_best:.0%} win chance, "
        f"{max(0.0, q_best - q_chosen):.0%} ahead of {chosen_label}."
    )


def _marks(state: GameState, actor: int, chosen, best) -> list[dict]:
    marks = []
    chosen_cid = encode_action(chosen)
    d = describe_move(chosen_cid, state, actor)
    m = {"kind": d["kind"], "target": d["target"], "chosen": True}
    if best == chosen:
        m["best"] = True
    marks.append(m)
    if best != chosen:
        d2 = describe_move(encode_action(best), state, actor)
        marks.append({"kind": d2["kind"], "target": d2["target"], "best": True})
    return marks


def _record_attempt(user_id, sid: str, seed: int, seat: int, slot: str, kind: str,
                     chosen, best, regret: float, pct: float, f_chosen: dict, f_best: dict) -> None:
    if user_id is None:
        return
    entry = {
        "user_id": user_id,
        "drill": sid,
        "seed": seed,
        "seat": seat,
        "slot": slot,
        "kind": kind,
        "chosen": encode_action(chosen),
        "best": encode_action(best),
        "regret": round(regret, 4),
        "pct": round(pct, 3),
        "f_chosen": f_chosen,
        "f_best": f_best,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _ATTEMPTS_LOCK:
        _ATTEMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ATTEMPTS_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def _net(net_path: str | None):
    if net_path is None:
        return None
    from net.evaluator import get_evaluator  # lazy: keeps torch optional

    return get_evaluator(net_path)


class LabSession:
    def __init__(
        self, sid: str, seed: int, seat: int, user_id, sims: int, dets: int,
        net_path: str | None = LAB_NET_PATH,
    ):
        self.sid = sid
        self.seed = seed
        self.human = seat
        self.user_id = user_id
        self.state = new_game(seed)
        net = _net(net_path)
        # Single-seed, no root noise: drills want fast coarse feedback, not
        # puzzle-labeling's multi-seed admission rigor (PLACEMENT_LAB_SPEC §2).
        self.grader = MCTSEngine(simulations=sims, determinizations=dets, seed=seed * 31 + 7, net=net)
        self.events: list[dict] = []
        self.grades: list[dict] = []

    # --- turn loop ---

    def _apply(self, action, who: str) -> None:
        actor = self.state.player_to_act()
        label = describe_move(encode_action(action), self.state, actor)["label"]
        h_before = dict(self.state.players[self.human].resources)
        apply_action(self.state, action)
        mine = actor == self.human
        ev = {"who": who, "label": label}
        if action.type is ActionType.SETUP_PLACE_SETTLEMENT:
            ev["place"] = {"kind": "settlement", "vertex": action.vertex, "mine": mine}
        else:
            ev["place"] = {"kind": "road", "edge": action.edge, "mine": mine}
        gain = _gained(h_before, self.state.players[self.human].resources)
        if gain:
            ev["you_gain"] = gain
        self.events.append(ev)

    def advance(self) -> None:
        """Auto-play the OTHER seat's picks (the engine) until it's the
        human's turn or the drill ends (setup phase exits)."""
        while self.state.phase is Phase.SETUP:
            if self.state.player_to_act() == self.human:
                return
            self._apply(self.grader.select_action(self.state), "bot")

    def _find_action(self, codec_id: int):
        for a in legal_actions(self.state):
            if encode_action(a) == codec_id:
                return a
        return None

    def submit(self, codec_id: int) -> dict:
        """Grade `codec_id` against the deep search pass BEFORE applying
        it (PLACEMENT_LAB_SPEC §2), log the attempt, then apply."""
        action = self._find_action(codec_id)
        if action is None:
            return {"illegal": True}
        actor = self.state.player_to_act()
        kind = "settlement" if action.type is ActionType.SETUP_PLACE_SETTLEMENT else "road"
        slot = _SLOT_FOR_INDEX[self.state.setup_index]

        evals = self.grader.evaluate(self.state, viewer=actor)
        best_eval = evals[0]
        rank = next(i for i, e in enumerate(evals) if e.action == action)
        chosen_eval = evals[rank]
        regret = max(0.0, best_eval.q - chosen_eval.q)
        points = points_for_regret(regret)
        pct = 1.0 if len(evals) < 2 else (len(evals) - 1 - rank) / (len(evals) - 1)

        f_chosen = _pick_facts(self.state, action, kind)
        f_best = _pick_facts(self.state, best_eval.action, kind)
        explanation = _explain_grade(
            self.state, actor, action, best_eval.action, best_eval.q, chosen_eval.q, kind
        )
        table = []
        for e in evals[:3]:
            d = describe_move(encode_action(e.action), self.state, actor)
            table.append({"label": d["label"], "q": round(e.q, 4),
                           "chosen": e.action == action, "best": e.action == best_eval.action})
        if not any(row["chosen"] for row in table):
            d = describe_move(encode_action(action), self.state, actor)
            table.append({"label": d["label"], "q": round(chosen_eval.q, 4),
                           "chosen": True, "best": False})

        grade = {
            "kind": kind,
            "slot": slot,
            "chosen_codec_id": encode_action(action),
            "best_codec_id": encode_action(best_eval.action),
            "regret": round(regret, 4),
            "points": points,
            "verdict": VERDICT_FOR_POINTS[points],
            "pct": round(pct, 3),
            "explanation": explanation,
            "table": table,
            "marks": _marks(self.state, actor, action, best_eval.action),
        }
        _record_attempt(
            self.user_id, self.sid, self.seed, self.human, slot, kind,
            action, best_eval.action, regret, pct, f_chosen, f_best,
        )
        self._apply(action, "you")
        self.grades.append(grade)
        self.advance()
        events, self.events = self.events, []
        return {"grade": grade, "events": events}

    # --- presentation ---

    def _prompt(self) -> str:
        st = self.state
        slot = _SLOT_FOR_INDEX[st.setup_index]
        turn_num = SETUP_ORDER[: st.setup_index + 1].count(self.human)
        ordinal = "first" if turn_num == 1 else "second"
        kind_word = "road" if st.awaiting_setup_road else "settlement"
        return f"Pick {turn_num} of 2 — {ordinal} {kind_word} ({slot})"

    def view(self) -> dict:
        st = self.state
        over = st.phase is not Phase.SETUP
        events, self.events = self.events, []
        payload = {
            "drill": self.sid,
            "seat": self.human,
            "layout": LAYOUT,
            "board": board_state(st, self.human),
            "context": context(st, self.human),
            "events": events,
            "moves": [],
            "slot": None,
            "prompt": "Drill complete." if over else self._prompt(),
        }
        if over:
            return payload
        actions = legal_actions(st)
        payload["moves"] = sorted(
            (describe_move(encode_action(a), st, self.human) for a in actions
             if encode_action(a) is not None),
            key=lambda d: d["codec_id"],
        )
        payload["slot"] = _SLOT_FOR_INDEX[st.setup_index]
        return payload

    def summary(self) -> dict:
        total = round(sum(g["regret"] for g in self.grades), 4)
        avg_points = round(sum(g["points"] for g in self.grades) / len(self.grades), 1) if self.grades else 0.0
        return {"grades": self.grades, "total_regret": total, "avg_points": avg_points}


class LabService:
    def __init__(self, sims: int = LAB_SIMS, dets: int = LAB_DETS, net_path: str | None = LAB_NET_PATH):
        self.sims = sims
        self.dets = dets
        self.net_path = net_path
        self._sessions: dict[str, LabSession] = {}
        self._lock = threading.Lock()
        self._rng = random.Random()
        self._last_seat: dict = {}

    def new_drill(self, user_id=None, seed: int | None = None) -> dict:
        with self._lock:
            if seed is None:
                seed = self._rng.randrange(LAB_SEED_BASE, LAB_SEED_BASE + _LAB_SEED_SPAN)
            seat = 1 - self._last_seat.get(user_id, 1)   # alternate per drill, per user
            self._last_seat[user_id] = seat
            sid = f"lab{seed}-{self._rng.randrange(1 << 30):08x}"
            s = LabSession(sid, seed, seat, user_id, self.sims, self.dets, net_path=self.net_path)
            self._sessions[sid] = s
            while len(self._sessions) > _MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            s.advance()
            return s.view()

    def act(self, sid: str, codec_id: int) -> dict:
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return {"error": "unknown or expired drill — start a new one"}
            result = s.submit(int(codec_id))
            if result.get("illegal"):
                return result
            over = s.state.phase is not Phase.SETUP
            result["next"] = None if over else s.view()
            result["summary"] = s.summary() if over else None
            return result


# --- bias analytics (PLACEMENT_LAB_SPEC §4) ---

_STATS_N = 50
_MIN_N = 15


def _load_settlement_attempts(user_id, limit: int = _STATS_N) -> list[dict]:
    if not _ATTEMPTS_PATH.exists():
        return []
    out = []
    with _ATTEMPTS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("user_id") == user_id and entry.get("kind") == "settlement":
                out.append(entry)
    return out[-limit:]


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def stats_for(user_id) -> dict:
    attempts = _load_settlement_attempts(user_id)
    n = len(attempts)
    by_slot = {}
    for slot in ("A1", "B1", "B2", "A2"):
        rs = [a["regret"] for a in attempts if a["slot"] == slot]
        by_slot[slot] = {"n": len(rs), "mean_regret": _mean(rs) if rs else None}

    if n == 0:
        return {
            "n": 0, "by_slot": by_slot, "deltas": None,
            "statements": [{"kind": "none", "statement": "No clear biases yet — keep drilling."}],
        }

    d_pips = _mean([a["f_chosen"]["pips"] - a["f_best"]["pips"] for a in attempts])
    d_div = _mean([a["f_chosen"]["div"] - a["f_best"]["div"] for a in attempts])
    d_ore = _mean([a["f_chosen"]["ore_share"] - a["f_best"]["ore_share"] for a in attempts])
    d_spots = _mean([a["f_chosen"]["spots"] - a["f_best"]["spots"] for a in attempts])
    port_rate_you = round(sum(1 for a in attempts if a["f_chosen"]["port"]) / n, 3)
    port_rate_engine = round(sum(1 for a in attempts if a["f_best"]["port"]) / n, 3)

    statements = []
    if n >= _MIN_N:
        if d_ore > 0.08:
            statements.append({"kind": "ore_share", "statement": "You overvalue ore-heavy spots."})
        if d_div < -0.5:
            statements.append({"kind": "diversity", "statement": "You give up resource diversity the engine keeps."})
        if d_spots < -0.7:
            statements.append({"kind": "spots", "statement": "You pick tight spots; the engine keeps expansion room."})
        if d_pips < -1.5:
            statements.append({"kind": "pips", "statement": "You leave raw production on the table."})

    first = [a["regret"] for a in attempts if a["slot"] in ("A1", "B1")]
    second = [a["regret"] for a in attempts if a["slot"] in ("A2", "B2")]
    if len(first) >= _MIN_N and len(second) >= _MIN_N:
        m1, m2 = _mean(first), _mean(second)
        # "much greater than": at least 50% relatively worse and not just
        # noise near zero (an absolute floor keeps two near-zero means from
        # triggering on a 1.6x ratio of tiny numbers).
        if m2 > m1 * 1.5 and (m2 - m1) > 0.02:
            statements.append({
                "kind": "second_pick",
                "statement": "Your SECOND pick is the leak — drill boards where the best seats are taken.",
            })

    if not statements:
        statements = [{"kind": "none", "statement": "No clear biases yet — keep drilling."}]

    return {
        "n": n,
        "by_slot": by_slot,
        "deltas": {
            "pips": d_pips, "diversity": d_div, "ore_share": d_ore, "spots": d_spots,
            "port_rate_you": port_rate_you, "port_rate_engine": port_rate_engine,
        },
        "statements": statements[:3],
    }
