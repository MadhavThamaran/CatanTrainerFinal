"""Post-game review (REVIEW_SPEC): score every human decision of a
finished play-mode game against a deeper search pass, chess.com-style.

Games are deterministic from (seed, action log) (engine guarantee since
M1), so a stored `data/games/<sid>.json` record replays exactly. Scoring
reuses the same primitives as puzzle labeling — `MCTSEngine.evaluate` for
ranked per-move Q-values, `points_for_regret` for the points table — just
run once per decision instead of the multi-seed admission pass.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from engine import GameState, apply_action, legal_actions, new_game
from net.codec import encode_action
from search import MCTSEngine

from .actions import describe_move
from .play import _label, action_from_dict
from .service import board_state
from puzzles.explain import move_facts, render
from puzzles.scoring import VERDICT_FOR_POINTS, points_for_regret

REVIEW_SIMS = 512
REVIEW_DETS = 6
_REVIEW_SEED = 909090

_GAMES_DIR = Path("data/games")


def _net(net_path: str | None):
    if net_path is None:
        return None
    from net.evaluator import get_evaluator  # lazy: keeps torch optional

    return get_evaluator(net_path)


def _count_scorable(record: dict) -> int:
    """Fast pass (no evaluation): how many rows this review will produce."""
    state = new_game(record["seed"])
    human = record["human"]
    n = 0
    for entry in record["log"]:
        if entry["actor"] == human and len(legal_actions(state)) > 1:
            n += 1
        apply_action(state, action_from_dict(entry["action"]))
    return n


def _mark(action, state: GameState, actor: int, flag: str) -> dict | None:
    cid = encode_action(action)
    if cid is None:
        return None  # discard or other non-encodable action: list-only
    d = describe_move(cid, state, actor)
    return {"kind": d["kind"], "target": d["target"], flag: True}


def _build_row(i: int, state: GameState, actor: int, action, evals) -> dict:
    best_eval = evals[0]
    chosen_eval = next(e for e in evals if e.action == action)
    q_best, q_chosen = best_eval.q, chosen_eval.q
    regret = max(0.0, q_best - q_chosen)
    points = points_for_regret(regret)

    chosen_cid = encode_action(action)
    chosen_label = (
        describe_move(chosen_cid, state, actor)["label"]
        if chosen_cid is not None
        else _label(action, state, actor)
    )
    best_cid = encode_action(best_eval.action)
    best_label = (
        describe_move(best_cid, state, actor)["label"]
        if best_cid is not None
        else _label(best_eval.action, state, actor)
    )

    marks = []
    chosen_mark = _mark(action, state, actor, "chosen")
    if chosen_mark:
        marks.append(chosen_mark)
    if best_eval.action != action:
        best_mark = _mark(best_eval.action, state, actor, "best")
        if best_mark:
            marks.append(best_mark)
    elif chosen_mark:
        chosen_mark["best"] = True  # chosen == best: one mark, both flags

    return {
        "i": i,
        "nth_decision": i + 1,
        "turn": state.turn_count,
        "chosen": {"label": chosen_label, "q": round(q_chosen, 4), "points": points},
        "best": {"label": best_label, "q": round(q_best, 4)},
        "regret": round(regret, 4),
        "verdict": VERDICT_FOR_POINTS[points],
        "win_prob": round(q_best, 4),
        "board": board_state(state, actor),
        "marks": marks,
        "why": _explain_row(state, actor, action, evals),
    }


def _explain_row(state, actor: int, action, evals) -> str | None:
    """EXPLAIN_SPEC §1: same facts->render pipeline as puzzles, one line
    per reviewed decision — contrasted against what the human actually
    played (or the runner-up, when they found the best move)."""
    best_action = evals[0].action
    alt_action = action if action != best_action else (
        evals[1].action if len(evals) > 1 else None
    )
    facts_best = move_facts(state, best_action, actor)
    facts_alt = move_facts(state, alt_action, actor) if alt_action is not None else None
    return render(facts_best, facts_alt, phase=None)


class ReviewService:
    """One review runs at a time (single background worker thread);
    finished reviews cache to disk so a reload is instant."""

    def __init__(self, sims: int = REVIEW_SIMS, dets: int = REVIEW_DETS):
        self.sims = sims
        self.dets = dets
        self._lock = threading.Lock()
        self._running: str | None = None
        self._results: dict[str, list[dict]] = {}
        self._done: dict[str, bool] = {}
        self._total: dict[str, int] = {}

    def _cache_path(self, game_id: str) -> Path:
        return _GAMES_DIR / f"{game_id}.review.json"

    def start(self, game_id: str) -> dict:
        cache = self._cache_path(game_id)
        if cache.exists():
            data = json.loads(cache.read_text())
            self._results[game_id] = data["results"]
            self._done[game_id] = True
            self._total[game_id] = data["total"]
            return {"review_id": game_id, "total": data["total"]}

        record_path = _GAMES_DIR / f"{game_id}.json"
        if not record_path.exists():
            return {"error": "unknown game"}

        with self._lock:
            if self._running is not None and not self._done.get(self._running, True):
                return {"error": "a review is already running", "status": 409}
            record = json.loads(record_path.read_text())
            total = _count_scorable(record)
            self._results[game_id] = []
            self._done[game_id] = False
            self._total[game_id] = total
            self._running = game_id
            t = threading.Thread(
                target=self._run, args=(game_id, record), daemon=True
            )
            t.start()
        return {"review_id": game_id, "total": total}

    def poll(self, review_id: str, frm: int) -> dict:
        results = self._results.get(review_id)
        if results is None:
            return {"error": "unknown review"}
        return {
            "done": self._done.get(review_id, False),
            "total": self._total.get(review_id, 0),
            "results": results[frm:],
        }

    def _run(self, review_id: str, record: dict) -> None:
        seed, human = record["seed"], record["human"]
        reviewer = MCTSEngine(
            simulations=self.sims,
            determinizations=self.dets,
            seed=_REVIEW_SEED,
            net=_net(record["bot"]),
        )
        reviewer.begin_game(human)
        state = new_game(seed)
        i = 0
        for entry in record["log"]:
            actor = entry["actor"]
            action = action_from_dict(entry["action"])
            if actor == human and len(legal_actions(state)) > 1:
                evals = reviewer.evaluate(state, viewer=human)
                row = _build_row(i, state, actor, action, evals)
                self._results[review_id].append(row)
                i += 1
            apply_action(state, action)
            reviewer.observe(state, action)
        final_vp = [state.total_vp(0), state.total_vp(1)]
        assert final_vp == record["final_vp"], (
            f"replay-integrity check failed: {final_vp} != {record['final_vp']}"
        )
        self._total[review_id] = i
        self._cache_path(review_id).write_text(
            json.dumps({"total": i, "results": self._results[review_id]})
        )
        self._done[review_id] = True
        with self._lock:
            if self._running == review_id:
                self._running = None
