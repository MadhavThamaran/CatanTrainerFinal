"""The analysis board (ANALYSIS_SPEC): branch, explore, and choose the
dice from any position the system already trusts — a played game record
or a puzzle. Perfect information (both hands revealed, like chess
analysis) and real search; no manual position editor in v1.

A session holds a node TREE, not a single position: nodes are
content-addressed by (parent, codec_id, forced) so re-exploring the same
forced line reuses the node and its cached eval, while unforced/"random"
applies always fork a fresh node (genuine resampling, per spec).
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
from pathlib import Path

from engine import (
    DevCard, GameState, Phase, Resource,
    apply_action, force_next_draw, force_next_roll, force_next_steal,
    legal_actions, new_game,
)
from net.codec import encode_action
from search import MCTSEngine

from .actions import describe_move
from .layout import LAYOUT
from .play import action_from_dict
from .service import board_state, context

ANALYSIS_SIMS = 512
ANALYSIS_DETS = 6
ANALYSIS_NET_PATH = "checkpoints/gen7.pt"
_MAX_SESSIONS = 4
_MAX_NODES = 200
_TOP_LINES = 8

_GAMES_DIR = Path("data/games")


def _net(net_path: str | None):
    if net_path is None:
        return None
    from net.evaluator import get_evaluator  # lazy: keeps torch optional

    return get_evaluator(net_path)


def _node_id(parent_id: str, codec_id: int, salt) -> str:
    key = f"{parent_id}|{codec_id}|{json.dumps(salt, sort_keys=True) if salt else ''}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _apply_forced(state: GameState, forced: dict) -> None:
    if "roll" in forced:
        force_next_roll(state, int(forced["roll"]))
    elif "steal" in forced:
        force_next_steal(state, Resource(forced["steal"]))
    elif "draw" in forced:
        force_next_draw(state, DevCard(forced["draw"]))


def _revealed(state: GameState, actor: int) -> dict:
    """Perfect-info panel (ANALYSIS_SPEC §2): the opponent's real hand and
    dev cards, plus the remaining deck's true composition (counts, not
    draw order — the chance picker needs odds, not a further spoiler)."""
    opp = 1 - actor
    ops = state.players[opp]
    return {
        "opp_hand": {r.value: n for r, n in ops.resources.items() if n},
        "opp_devs": {c.value: n for c, n in ops.dev_cards.items() if n},
        "deck_count": len(state.dev_deck),
        "deck": {c.value: n for c in DevCard if (n := state.dev_deck.count(c))},
    }


class AnalysisSession:
    def __init__(self, aid: str, root_state: GameState, sims: int, dets: int, net_path: str | None):
        self.aid = aid
        self.states: dict[str, GameState] = {"root": root_state}
        self.parent: dict[str, str | None] = {"root": None}
        self.label: dict[str, str] = {"root": "start"}
        self.evals: dict[str, list[dict]] = {}
        self._nonce = 0
        # exact=True (ANALYSIS_SPEC §4): these states are already perfect,
        # so the K "worlds" differ only in search randomness, not hidden info.
        self.engine = MCTSEngine(
            simulations=sims, determinizations=dets, seed=1,
            net=_net(net_path), exact=True,
        )

    def node_view(self, node_id: str) -> dict:
        state = self.states[node_id]
        actor = state.player_to_act()
        over = state.phase is Phase.GAME_OVER
        return {
            "analysis_id": self.aid,
            "node": node_id,
            "parent": self.parent[node_id],
            "game_over": over,
            "winner": (
                None if not over
                else "p0" if state.winner == 0 else "p1" if state.winner == 1 else "draw"
            ),
            "layout": LAYOUT,
            "board": board_state(state, actor),
            "context": context(state, actor),
            "revealed": _revealed(state, actor),
            # ANALYSIS_SPEC §3: the chance picker's odds — the simulator's
            # OWN adjusted distribution, not a generic 2d6 assumption.
            "dice_probs": None if over else {
                t: round(p, 4) for t, p in state.dice.probabilities(actor).items()
            },
            "moves": [] if over else sorted(
                (
                    describe_move(encode_action(a), state, actor)
                    for a in legal_actions(state)
                    if encode_action(a) is not None
                ),
                key=lambda d: d["codec_id"],
            ),
        }

    def eval_node(self, node_id: str) -> list[dict]:
        if node_id not in self.evals:
            state = self.states[node_id]
            if state.phase is Phase.GAME_OVER:
                self.evals[node_id] = []
            else:
                actor = state.player_to_act()
                ranked = self.engine.evaluate(state, viewer=actor)
                self.evals[node_id] = [
                    {**describe_move(cid, state, actor), "q": round(e.q, 4), "visits": e.visits}
                    for e in ranked[:_TOP_LINES]
                    if (cid := encode_action(e.action)) is not None
                ]
        return self.evals[node_id]

    def apply(self, node_id: str, codec_id: int, forced: dict | None) -> dict:
        state = self.states[node_id]
        action = next((a for a in legal_actions(state) if encode_action(a) == codec_id), None)
        if action is None:
            return {"error": "illegal move"}

        if forced:
            child_id = _node_id(node_id, codec_id, forced)
            if child_id in self.states:
                return {"node": self.node_view(child_id)}   # same forced line: reuse
        else:
            self._nonce += 1
            child_id = _node_id(node_id, codec_id, {"_n": self._nonce})   # always fresh

        if len(self.states) >= _MAX_NODES:
            return {"error": "line too deep, start a new analysis"}

        new_state = state.clone()
        if forced:
            _apply_forced(new_state, forced)
        actor = state.player_to_act()
        label = describe_move(codec_id, state, actor)["label"]
        if forced:
            label += f" (forced {next(iter(forced))}={next(iter(forced.values()))})"
        apply_action(new_state, action)

        self.states[child_id] = new_state
        self.parent[child_id] = node_id
        self.label[child_id] = label
        return {"node": self.node_view(child_id)}

    def tree(self) -> dict:
        return {
            nid: {
                "parent": self.parent[nid],
                "label": self.label[nid],
                "game_over": self.states[nid].phase is Phase.GAME_OVER,
            }
            for nid in self.states
        }


def _replay_game(sid: str, index: int | None) -> GameState | None:
    path = _GAMES_DIR / f"{sid}.json"
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    state = new_game(record["seed"])
    log = record["log"]
    stop = len(log) if index is None else max(0, min(int(index), len(log)))
    for entry in log[:stop]:
        apply_action(state, action_from_dict(entry["action"]))
    return state


class AnalysisService:
    def __init__(
        self,
        puzzle_by_id=None,             # Callable[[str], Puzzle | None]
        sims: int = ANALYSIS_SIMS,
        dets: int = ANALYSIS_DETS,
        net_path: str | None = ANALYSIS_NET_PATH,
    ):
        self.puzzle_by_id = puzzle_by_id
        self.sims = sims
        self.dets = dets
        self.net_path = net_path
        self._sessions: dict[str, AnalysisSession] = {}
        self._lock = threading.Lock()
        self._rng = random.Random()

    def _root_state(self, source: str, id_: str, index: int | None) -> GameState | None:
        if source == "game":
            return _replay_game(id_, index)
        if source == "puzzle":
            p = self.puzzle_by_id(id_) if self.puzzle_by_id else None
            return GameState.from_dict(p.state) if p is not None else None
        return None

    def new_session(self, source: str, id_: str, index: int | None = None) -> dict:
        with self._lock:
            state = self._root_state(source, id_, index)
            if state is None:
                return {"error": f"no such {source}: {id_}"}
            aid = f"an{self._rng.randrange(1 << 48):012x}"
            s = AnalysisSession(aid, state, self.sims, self.dets, self.net_path)
            self._sessions[aid] = s
            while len(self._sessions) > _MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            return {"analysis_id": aid, "root": s.node_view("root")}

    def _get(self, analysis_id: str) -> AnalysisSession | None:
        return self._sessions.get(analysis_id)

    def eval(self, analysis_id: str, node: str) -> dict:
        s = self._get(analysis_id)
        if s is None:
            return {"error": "unknown or expired analysis"}
        if node not in s.states:
            return {"error": "unknown node"}
        return {"lines": s.eval_node(node)}

    def apply(self, analysis_id: str, node: str, codec_id: int, forced: dict | None = None) -> dict:
        s = self._get(analysis_id)
        if s is None:
            return {"error": "unknown or expired analysis"}
        if node not in s.states:
            return {"error": "unknown node"}
        return s.apply(node, int(codec_id), forced)

    def tree(self, analysis_id: str) -> dict:
        s = self._get(analysis_id)
        if s is None:
            return {"error": "unknown or expired analysis"}
        return {"nodes": s.tree()}
