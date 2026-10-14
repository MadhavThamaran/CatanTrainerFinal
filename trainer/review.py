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
import os
import re
import threading
from pathlib import Path

from engine import ActionType, GameState, apply_action, legal_actions, new_game
from net.codec import encode_action
from search import MCTSEngine

from . import ladder
from .actions import describe_move
from .dashboard import game_accuracy, visible_to
from .layout import LAYOUT
from .play import _label, action_from_dict
from .service import board_state, context
from puzzles.explain import move_facts, render
from puzzles.scoring import VERDICT_FOR_POINTS, points_for_regret

REVIEW_SIMS = 512
REVIEW_DETS = 6
_REVIEW_SEED = 909090

_GAMES_DIR = Path("data/games")
RECENT_GAMES = 20
# Game ids are `g<seed>-<hex>`; they become part of a file path, so anything else is not a game.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _valid_id(game_id) -> bool:
    return isinstance(game_id, str) and _SAFE_ID.match(game_id) is not None


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


def _build_row(i: int, log_index: int, state: GameState, actor: int, action, evals) -> dict:
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
        "log_index": log_index,   # ANALYSIS_SPEC entry point: replay the game to here
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


def _outcome(record: dict) -> str:
    """"you" | "bot" | "draw" — the winner as the play screen words it."""
    winner = record.get("winner")
    return "draw" if winner is None else "you" if winner == record["human"] else "bot"


def _rung_of(record: dict) -> tuple[int | None, str]:
    """(rung, name) when the record was played against the CURRENT rung table; otherwise
    (None, "Earlier game"): its rung number described a different bot (`LADDER_VERSION`)."""
    n = record.get("rung")
    if record.get("ladder_version") == ladder.LADDER_VERSION and n is not None:
        try:
            return n, ladder.get_rung(n).name
        except KeyError:
            pass
    return None, "Earlier game"


def _finished_view(game_id: str, record: dict, reviewed: bool) -> dict:
    """A stored game as the play screen shows it right after game over — the same payload shape
    `PlaySession.view()` builds, with the game log rebuilt by replaying the record — so the client
    renders a reopened game with the code it uses for a live one. Raises ValueError when the record
    no longer replays to the result it was stored with (the engine changed since it was played)."""
    human = record["human"]
    state = new_game(record["seed"])
    events = []
    for entry in record["log"]:
        actor = entry["actor"]
        action = action_from_dict(entry["action"])
        ev = {"who": "you" if actor == human else "bot", "label": _label(action, state, actor)}
        apply_action(state, action)
        if action.type is ActionType.ROLL:
            ev["roll"] = state.last_roll
        if "verdict" in entry:                       # coached moves keep their verdict (COACH_SPEC §3)
            ev["verdict"] = entry["verdict"]
        events.append(ev)
    if [state.total_vp(0), state.total_vp(1)] != record["final_vp"]:
        raise ValueError("this game no longer replays to its recorded result")
    rung, rung_name = _rung_of(record)
    return {
        "session": game_id,
        "game_over": True,
        "winner": _outcome(record),
        "your_seat": human,
        "rung": rung,
        "rung_name": rung_name,
        "rated": bool(record.get("rated")),
        "ladder": None,
        "layout": LAYOUT,
        "board": board_state(state, human),
        "context": context(state, human),
        "events": events,
        "moves": [],
        "discard": None,
        "prompt": "Game over.",
        "reviewed": reviewed,
    }


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
        self._errors: dict[str, str] = {}
        self._owners: dict[str, int | None] = {}

    def _cache_path(self, game_id: str) -> Path:
        return _GAMES_DIR / f"{game_id}.review.json"

    def running(self) -> str | None:
        """The game being reviewed right now, if any (the same test `start` applies)."""
        r = self._running
        return r if r is not None and not self._done.get(r, True) else None

    def _read_cache(self, game_id: str) -> dict | None:
        """The finished review cached for this game, or None when there is none or the file is torn
        or misshapen (the review is then simply recomputed)."""
        try:
            data = json.loads(self._cache_path(game_id).read_text())
        except (OSError, json.JSONDecodeError):
            return None
        ok = isinstance(data, dict) and isinstance(data.get("results"), list) and "total" in data
        return data if ok else None

    def _load_record(self, game_id) -> dict | None:
        if not _valid_id(game_id):
            return None
        try:
            return json.loads((_GAMES_DIR / f"{game_id}.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def list_games(self, user_id: int | None, limit: int = RECENT_GAMES) -> list[dict]:
        """The player's most recent finished games, newest first — what there is to reopen
        (REVIEW_SPEC §6). Records written before they carried a `user_id` stay visible to
        everyone, as on the dashboard."""
        if not _GAMES_DIR.exists():
            return []
        files = sorted(
            (p for p in _GAMES_DIR.glob("*.json") if not p.name.endswith(".review.json")),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        games = []
        for p in files:
            record = self._load_record(p.stem)
            if (record is None or not {"seed", "human", "log", "final_vp"} <= record.keys()
                    or not visible_to(record, user_id)):
                continue
            rev = self._read_cache(p.stem)
            rung, rung_name = _rung_of(record)
            human = record["human"]
            accuracy = game_accuracy(rev) if rev else None
            games.append({
                "id": p.stem,
                "ts": record.get("ts", ""),
                "rung": rung,
                "rung_name": rung_name,
                "rated": bool(record.get("rated")),
                "winner": _outcome(record),
                "final_vp": {"you": record["final_vp"][human], "bot": record["final_vp"][1 - human]},
                "reviewed": rev is not None,
                "accuracy": round(accuracy, 1) if accuracy is not None else None,
            })
            if len(games) >= limit:
                break
        games.sort(key=lambda g: g["ts"], reverse=True)
        return games

    def open_game(self, game_id, user_id: int | None) -> dict:
        """A finished game, rebuilt from its record, for the play screen (`_finished_view`)."""
        record = self._load_record(game_id)
        if record is None or not visible_to(record, user_id):
            return {"error": "unknown game"}
        try:
            return _finished_view(game_id, record, reviewed=self._cache_path(game_id).exists())
        except Exception as exc:  # noqa: BLE001 — the engine asserts legality: a stale record raises AssertionError
            return {"error": f"this game can no longer be reopened ({type(exc).__name__}: {exc})"}

    def start(self, game_id: str, user_id: int | None = None) -> dict:
        if not _valid_id(game_id):
            return {"error": "unknown game"}
        record = self._load_record(game_id)
        if record is None or not visible_to(record, user_id):
            return {"error": "unknown game"}
        owner = record.get("user_id")

        data = self._read_cache(game_id)
        if data is not None:
            self._results[game_id] = data["results"]
            self._done[game_id] = True
            self._total[game_id] = data["total"]
            self._errors.pop(game_id, None)
            self._owners[game_id] = owner
            return {"review_id": game_id, "total": data["total"]}

        try:
            total = _count_scorable(record)
        except Exception as exc:  # noqa: BLE001 — a stale record trips the engine's legality asserts
            return {"error": f"this game can no longer be reviewed ({type(exc).__name__}: {exc})"}

        with self._lock:
            running = self._running is not None and not self._done.get(self._running, True)
            if running and self._running == game_id:
                # Reopened mid-review (page reload, back to the game list): join it.
                return {"review_id": game_id, "total": self._total[game_id]}
            if running:
                return {"error": "a review is already running", "status": 409}
            self._results[game_id] = []
            self._done[game_id] = False
            self._total[game_id] = total
            self._errors.pop(game_id, None)
            self._owners[game_id] = owner
            self._running = game_id
            t = threading.Thread(
                target=self._run, args=(game_id, record), daemon=True
            )
            t.start()
        return {"review_id": game_id, "total": total}

    def poll(self, review_id: str, frm: int, user_id: int | None = None) -> dict:
        results = self._results.get(review_id)
        owner = self._owners.get(review_id)
        if results is None or not (owner is None or user_id is None or owner == user_id):
            return {"error": "unknown review"}
        out = {
            "done": self._done.get(review_id, False),
            "total": self._total.get(review_id, 0),
            "results": results[frm:],
        }
        if review_id in self._errors:
            out["failed"] = self._errors[review_id]    # `done` is then True: the worker has stopped
        return out

    def _run(self, review_id: str, record: dict) -> None:
        try:
            self._review(review_id, record)
        except Exception as exc:  # noqa: BLE001 — a worker that dies silently would leave the
            # review "running" forever, and the single-flight lock would refuse every other one
            self._errors[review_id] = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:                       # done and not-running flip together
                self._done[review_id] = True
                if self._running == review_id:
                    self._running = None

    def _review(self, review_id: str, record: dict) -> None:
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
        for log_index, entry in enumerate(record["log"]):
            actor = entry["actor"]
            action = action_from_dict(entry["action"])
            if actor == human and len(legal_actions(state)) > 1:
                evals = reviewer.evaluate(state, viewer=human)
                row = _build_row(i, log_index, state, actor, action, evals)
                self._results[review_id].append(row)
                i += 1
            apply_action(state, action)
            reviewer.observe(state, action)
        final_vp = [state.total_vp(0), state.total_vp(1)]
        assert final_vp == record["final_vp"], (
            f"replay-integrity check failed: {final_vp} != {record['final_vp']}"
        )
        self._total[review_id] = i
        cache = self._cache_path(review_id)
        tmp = cache.parent / (cache.name + ".tmp")     # a crash can't leave a torn cache at the real path
        tmp.write_text(json.dumps({"total": i, "results": self._results[review_id]}))
        self._publish(tmp, cache)

    @staticmethod
    def _publish(tmp: Path, cache: Path) -> None:
        os.replace(tmp, cache)                         # atomic: readers see the whole review or none
