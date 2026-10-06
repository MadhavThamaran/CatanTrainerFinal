"""Play-vs-bot mode: live games against a bot ladder rung (LADDER_SPEC).

Sessions hold a real `GameState`; the human sees the observation-level
view (the bot's hand stays server-side). The bot is whatever `Agent` the
chosen rung resolves to (`trainer/ladder.py::make_bot`) — HeuristicAgent,
raw MCTS, or net-guided MCTS — fed observations through the normal Agent
hooks so a search-based bot's CardTracker keeps exact beliefs about the
human's hand, the same machinery it uses in gated matches.

Turn loop: after every human action the service auto-plays everything that
is not a human decision — the bot's whole turn and any forced single-action
steps for the human (dice rolls) — and returns the next real decision
point plus an event log of what happened in between.

Discards are the one action the codec cannot express: when the human must
discard, the payload carries {"discard": {"count": k}} and the client
submits a resource multiset instead of a codec id.
"""
from __future__ import annotations

import json
import random
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from engine import (
    Action, ActionType, GameState, Phase, Resource,
    apply_action, legal_actions, new_game,
)
from net.codec import encode_action
from search import MCTSEngine

from puzzles.scoring import VERDICT_FOR_POINTS, points_for_regret

from . import ladder
from .actions import describe_move
from .layout import LAYOUT
from .service import board_state, context

_MAX_SESSIONS = 8
_MAX_ACTIONS = 6000

# COACH_SPEC §3: the coach judges at a fixed, strong reference regardless of
# the ladder rung you're playing — its job is to grade YOUR moves honestly,
# not to get weaker just because you picked an easy opponent.
COACH_SIMS = 160
COACH_DETS = 4
COACH_NET_PATH = "checkpoints/gen7.pt"
COACH_SEVERITY = {"strict": 0.05, "normal": 0.10, "blunders-only": 0.15}

DEFAULT_RUNG = 6   # Master: gen7 s=160 k=4 — the pre-ladder default bot


_GAMES_DIR = Path("data/games")


def action_to_dict(a: Action) -> dict:
    """Faithful serialization of the frozen Action (REVIEW_SPEC §2a)."""
    d = {"type": a.type.name, "player": a.player}
    for f in ("vertex", "edge", "hex"):
        if getattr(a, f) is not None:
            d[f] = getattr(a, f)
    if a.give is not None:
        d["give"] = a.give.value
    if a.get is not None:
        d["get"] = a.get.value
    if a.resources is not None:
        d["resources"] = [r.value for r in a.resources]
    return d


def action_from_dict(d: dict) -> Action:
    return Action(
        ActionType[d["type"]],
        d["player"],
        vertex=d.get("vertex"),
        edge=d.get("edge"),
        hex=d.get("hex"),
        give=Resource(d["give"]) if "give" in d else None,
        get=Resource(d["get"]) if "get" in d else None,
        resources=tuple(Resource(r) for r in d["resources"])
        if "resources" in d
        else None,
    )


def _gained(before: dict, after: dict) -> dict:
    """Positive per-resource delta, as {resource_name: n}."""
    out = {}
    for r, n in after.items():
        d = n - before.get(r, 0)
        if d > 0:
            out[r.value] = d
    return out


def _label(action, state: GameState, actor: int) -> str:
    cid = encode_action(action)
    if cid is not None:
        return describe_move(cid, state, actor)["label"]
    if action.type is ActionType.DISCARD:
        counts = Counter(r.value for r in action.resources)
        return "Discard " + ", ".join(f"{n} {r}" for r, n in sorted(counts.items()))
    return action.type.name.replace("_", " ").title()


def _category(action, state: GameState, actor: int) -> str:
    """Coarse move-kind tag for the coach's spoiler-free hint (COACH_SPEC
    §2): the codec's own `category` field, or "discard" for the one
    action type the codec can't express."""
    cid = encode_action(action)
    if cid is not None:
        return describe_move(cid, state, actor)["category"]
    return "discard"


class PlaySession:
    def __init__(
        self,
        sid: str,
        seed: int,
        rung: int,
        rated: bool,
        ratings=None,   # trainer.elo.Ratings for the human, or None (anonymous/test)
        coach: bool = False,
        coach_sims: int = COACH_SIMS,
        coach_dets: int = COACH_DETS,
        user_id: int | None = None,   # stamped on the game record (dashboard filters by it)
    ):
        self.sid = sid
        self.seed = seed
        self.rung = rung
        self.rated = rated
        self.ratings = ratings
        self.user_id = user_id
        # Kept for REVIEW_SPEC (`record["bot"]`, reviewed at deeper search
        # over the SAME net the bot used — None for the no-net rungs).
        self.net_path = ladder.get_rung(rung).net_path
        self.log: list[dict] = []      # replayable record (REVIEW_SPEC §2a)
        self._persisted = False
        self._ladder_deltas: dict | None = None
        self.state = new_game(seed)
        self.human = seed % 2          # alternate seats across seeds
        self.bot_seat = 1 - self.human
        self.bot = ladder.make_bot(rung, seed=seed * 31 + 7)
        self.bot.begin_game(self.bot_seat)
        # COACH_SPEC §3: judged from the human's information set — always
        # built (its cost is opt-in via evaluate() calls, gated by
        # coach_on) so a mid-game toggle-on has an up-to-date CardTracker
        # instead of a blind one.
        # LADDER_SPEC §2: rated games are coach-free — a coach would hand out
        # the very answers the rating is meant to measure.
        self.coach_on = coach and not rated
        self.coach_severity = "normal"
        from net.evaluator import get_evaluator

        self.coach_engine = MCTSEngine(
            simulations=coach_sims,
            determinizations=coach_dets,
            seed=seed * 31 + 13,
            net=get_evaluator(COACH_NET_PATH),
        )
        self.coach_engine.begin_game(self.human)
        self._coach_cache: tuple[int, list] | None = None
        # One entry per flagged move (COACH_SPEC §3), persisted with the game
        # so the dashboard can read interjection / played-anyway / hint rates.
        self.coach_events: list[dict] = []
        self._open_interjection: dict | None = None   # bounce awaiting the human's next move
        self.events: list[dict] = []
        self.n_actions = 0

    # --- turn loop ---

    def _apply(self, action, who: str, coach_verdict: dict | None = None) -> None:
        actor = self.state.player_to_act()
        label = _label(action, self.state, actor)
        h_before = dict(self.state.players[self.human].resources)
        b_before = dict(self.state.players[self.bot_seat].resources)
        apply_action(self.state, action)
        self.bot.observe(self.state, action)
        self.coach_engine.observe(self.state, action)
        self._coach_cache = None   # any application invalidates the cached decision
        ev = {"who": who, "label": label}
        if coach_verdict is not None:
            ev.update(coach_verdict)
        mine = actor == self.human
        # Board mutations ride on the event so the client can animate the
        # board piece-by-piece during playback instead of snapping at the end.
        if action.type in (
            ActionType.SETUP_PLACE_SETTLEMENT, ActionType.BUILD_SETTLEMENT
        ):
            ev["place"] = {"kind": "settlement", "vertex": action.vertex, "mine": mine}
        elif action.type is ActionType.BUILD_CITY:
            ev["place"] = {"kind": "city", "vertex": action.vertex, "mine": mine}
        elif action.type in (ActionType.SETUP_PLACE_ROAD, ActionType.BUILD_ROAD):
            ev["place"] = {"kind": "road", "edge": action.edge, "mine": mine}
        elif action.type is ActionType.MOVE_ROBBER:
            ev["robber"] = action.hex
        if action.type is ActionType.ROLL:
            ev["roll"] = self.state.last_roll
            # Production is public information: both players' roll gains
            # are announced (the client animates them).
            ev["you_gain"] = _gained(h_before, self.state.players[self.human].resources)
            ev["bot_gain"] = _gained(b_before, self.state.players[self.bot_seat].resources)
        else:
            # The human always sees their own hand change (steals, monopoly,
            # build costs, trades); the bot's non-roll changes stay counts.
            h_after = self.state.players[self.human].resources
            gain = _gained(h_before, h_after)
            lose = _gained(h_after, h_before)
            if gain:
                ev["you_gain"] = gain
            if lose:
                ev["you_lose"] = lose
        self.events.append(ev)
        entry = {"actor": actor, "action": action_to_dict(action)}
        if coach_verdict is not None:
            entry.update(coach_verdict)   # coached moves keep their verdict (COACH_SPEC §3)
        self.log.append(entry)
        self.n_actions += 1
        if self.state.phase is Phase.GAME_OVER:
            self._persist()

    def _persist(self) -> None:
        """Write the replayable game record (feeds post-game review) and,
        once (LADDER_SPEC §3), settle the ladder: unlocks always move on a
        win; play-Elo/W-L/stars move only in rated games."""
        if self._persisted:
            return
        self._persisted = True
        winner_str = (
            "you" if self.state.winner == self.human
            else "bot" if self.state.winner == self.bot_seat
            else "draw"
        )
        if self.ratings is not None:
            self._ladder_deltas = ladder.record_game(
                self.ratings.ladder, self.rung, self.rated, winner_str
            )
            self.ratings.save()
        _GAMES_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "sid": self.sid,
            "seed": self.seed,
            "human": self.human,
            "bot": self.net_path,
            "rung": self.rung,
            "rated": self.rated,
            "user_id": self.user_id,
            "log": self.log,
            "coach_events": self.coach_events,
            "winner": self.state.winner,
            "final_vp": [self.state.total_vp(0), self.state.total_vp(1)],
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (_GAMES_DIR / f"{self.sid}.json").write_text(json.dumps(record))

    # --- coach mode (COACH_SPEC §3) ---

    def _coach_evals(self):
        """Cached ranking of the CURRENT decision — keyed by n_actions so
        any application (including a confirmed coach interjection)
        invalidates it, but re-picking after "Think again" is instant."""
        if self._coach_cache is not None and self._coach_cache[0] == self.n_actions:
            return self._coach_cache[1]
        evals = self.coach_engine.evaluate(self.state, viewer=self.human)
        self._coach_cache = (self.n_actions, evals)
        return evals

    def coach_judge(self, action) -> tuple[float, int]:
        """(regret, points) of `action` against the coach's ranking of the
        position it's offered in."""
        evals = self._coach_evals()
        chosen = next((e for e in evals if e.action == action), None)
        regret = max(0.0, evals[0].q - chosen.q) if chosen is not None else 0.0
        return regret, points_for_regret(regret)

    def coach_interjection(self, action) -> dict | None:
        """None if `action` clears the severity bar; otherwise the
        spoiler-free payload the client shows instead of applying it."""
        regret, _ = self.coach_judge(action)
        threshold = COACH_SEVERITY[self.coach_severity]
        if regret < threshold:
            return None
        evals = self._coach_evals()
        best = evals[0].action
        return {
            "regret": round(regret, 4),
            "severity": "blunder" if regret >= COACH_SEVERITY["blunders-only"] else "mistake",
            "hint_category": _category(best, self.state, self.human),
        }

    def set_coach(self, on: bool) -> bool:
        """Toggle the coach. False (and unchanged) if refused: rated games
        never allow it (LADDER_SPEC §2)."""
        if on and self.rated:
            return False
        self.coach_on = on
        return True

    def note_coach_hint(self) -> None:
        """The human revealed the hint on the interjection now showing (the
        reveal itself is client-side, COACH_SPEC §2). No-op if none is open."""
        open_ = self._open_interjection
        if open_ is not None and open_["at"] == len(self.log):
            open_["hint"] = True

    def _log_interjection(self, action, info: dict) -> None:
        """Record a bounce. The same flagged move re-submitted after "Think
        again" is still one event; a different move means they changed their
        mind (and if that one is flagged too, it opens its own event). An
        event whose `outcome` stays None was abandoned."""
        at = len(self.log)   # log index the human's next applied action will take
        key = action_to_dict(action)
        open_ = self._open_interjection
        if open_ is not None and open_["at"] == at:
            if open_["action"] == key:
                return
            open_["outcome"] = "changed"
        self._open_interjection = {
            "at": at, "action": key, "regret": info["regret"],
            "severity": info["severity"], "hint": False, "outcome": None,
        }
        self.coach_events.append(self._open_interjection)

    def _close_interjection(self, action, confirm: bool) -> None:
        """The human is about to apply `action`: settle the open bounce."""
        open_ = self._open_interjection
        if open_ is not None and open_["at"] == len(self.log):
            played_anyway = confirm and action_to_dict(action) == open_["action"]
            open_["outcome"] = "confirmed" if played_anyway else "changed"
        self._open_interjection = None

    def advance(self) -> None:
        """Play until the human faces a real decision or the game ends."""
        while (
            self.state.phase is not Phase.GAME_OVER
            and self.n_actions < _MAX_ACTIONS
        ):
            actor = self.state.player_to_act()
            actions = legal_actions(self.state)
            if actor == self.human:
                # Auto-play forced steps (rolls, a lone forced road) — but
                # NEVER auto-end the human's turn: even with nothing to do,
                # ending it is theirs (colonist-style agency).
                if len(actions) == 1 and actions[0].type is not ActionType.END_TURN:
                    self._apply(actions[0], "you")
                    continue
                return  # human decision point
            self._apply(
                actions[0] if len(actions) == 1
                else self.bot.select_action(self.state),
                "bot",
            )

    # --- human actions ---

    def _find_action(self, codec_id: int | None, discard: list[str] | None):
        if discard is not None:
            want = Counter(discard)
            for a in legal_actions(self.state):
                if a.type is ActionType.DISCARD and Counter(
                    r.value for r in a.resources
                ) == want:
                    return a
            return None
        if codec_id is None:
            return None
        for a in legal_actions(self.state):
            if encode_action(a) == codec_id:
                return a
        return None

    def submit(
        self,
        codec_id: int | None = None,
        discard: list[str] | None = None,
        confirm: bool = False,
    ) -> dict:
        """Find + (coach-gate +) apply a human action.

        `{"illegal": True}` if codec_id/discard didn't match a legal move;
        `{"coach": {...}}` if coach mode bounced it (state UNCHANGED —
        COACH_SPEC §1's no-side-effect property; the bounce is logged to
        `coach_events`); `{"applied": True}` once applied (the passive
        verdict badge, §4, rides on the event log)."""
        action = self._find_action(codec_id, discard)
        if action is None:
            return {"illegal": True}
        if self.coach_on and not confirm:
            interjection = self.coach_interjection(action)
            if interjection is not None:
                self._log_interjection(action, interjection)
                return {"coach": interjection}
        verdict = None
        if self.coach_on:
            _, points = self.coach_judge(action)
            verdict = {"verdict": VERDICT_FOR_POINTS[points]}
            if confirm:
                verdict["coached"] = "confirmed"
        self._close_interjection(action, confirm)
        self._apply(action, "you", coach_verdict=verdict)
        return {"applied": True}

    # --- presentation ---

    def view(self) -> dict:
        st = self.state
        over = st.phase is Phase.GAME_OVER or self.n_actions >= _MAX_ACTIONS
        if over:
            self._persist()   # covers action-cap draws too
        events, self.events = self.events, []   # drain: each event ships once
        rung_cfg = ladder.get_rung(self.rung)
        payload = {
            "session": self.sid,
            "game_over": over,
            "winner": (
                None if not over
                else "you" if st.winner == self.human
                else "bot" if st.winner == self.bot_seat
                else "draw"
            ),
            "your_seat": self.human,
            "rung": self.rung,
            "rung_name": rung_cfg.name,
            "rated": self.rated,
            "ladder": self._ladder_deltas,
            "layout": LAYOUT,
            "board": board_state(st, self.human),
            "context": context(st, self.human),
            "events": events,
            "moves": [],
            "discard": None,
            "prompt": "Game over." if over else "Your move.",
        }
        if over:
            return payload
        actions = legal_actions(st)
        if actions[0].type is ActionType.DISCARD:
            k = st.players[self.human].hand_size() // 2
            payload["discard"] = {"count": k}
            payload["prompt"] = f"Rolled 7 — discard {k} cards."
            return payload
        payload["moves"] = sorted(
            (
                describe_move(encode_action(a), st, self.human)
                for a in actions
                if encode_action(a) is not None
            ),
            key=lambda d: d["codec_id"],
        )
        if st.phase is Phase.SETUP:
            kind = actions[0].type
            payload["prompt"] = (
                "Setup: place a settlement."
                if kind is ActionType.SETUP_PLACE_SETTLEMENT
                else "Setup: place its road."
            )
        elif actions[0].type is ActionType.MOVE_ROBBER:
            payload["prompt"] = "Move the robber."
        elif st.free_roads > 0:
            payload["prompt"] = "Road Building: place your free road."
        return payload


class PlayService:
    def __init__(
        self,
        ratings_for=None,          # Callable[[int], Ratings] | None (tests: anonymous)
        default_rung: int = DEFAULT_RUNG,
        coach_sims: int = COACH_SIMS,
        coach_dets: int = COACH_DETS,
    ):
        self.ratings_for = ratings_for
        self.default_rung = default_rung
        self.coach_sims = coach_sims
        self.coach_dets = coach_dets
        self._sessions: dict[str, PlaySession] = {}
        self._lock = threading.Lock()
        self._rng = random.Random()

    def new_game(
        self,
        user_id: int | None = None,
        seed: int | None = None,
        rung: int | None = None,
        rated: bool = False,
        coach: bool = False,
    ) -> dict:
        rung = self.default_rung if rung is None else rung
        if not (ladder.MIN_RUNG <= rung <= ladder.MAX_RUNG):
            return {"error": f"no such rung: {rung}"}
        with self._lock:
            ratings = self.ratings_for(user_id) if (self.ratings_for and user_id is not None) else None
            if ratings is not None and not ladder.is_unlocked(ratings.ladder, rung):
                return {"error": f"rung {rung} is locked"}
            if seed is None:
                seed = self._rng.randrange(300_000, 400_000)
            sid = f"g{seed}-{self._rng.randrange(1 << 30):08x}"
            s = PlaySession(
                sid, seed, rung, rated, ratings=ratings,
                coach=coach, coach_sims=self.coach_sims, coach_dets=self.coach_dets,
                user_id=user_id,
            )
            self._sessions[sid] = s
            while len(self._sessions) > _MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            s.advance()
            return s.view()

    def act(
        self,
        sid: str,
        codec_id: int | None = None,
        discard: list[str] | None = None,
        confirm: bool = False,
        coach_set: bool | None = None,
        coach_hint: bool = False,
    ) -> dict:
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return {"error": "unknown or expired session — start a new game"}
            if coach_set is not None:
                # Toggle only (COACH_SPEC §3): evaluations simply start/stop
                # from here on — the CardTracker stays caught up either way
                # since `_apply` observes it unconditionally.
                if not s.set_coach(bool(coach_set)):
                    return {"error": "the coach is disabled in rated games"}
                return s.view()
            if coach_hint:
                s.note_coach_hint()
                return {"ok": True}
            if s.state.phase is Phase.GAME_OVER:
                return {"error": "game is over"}
            if s.state.player_to_act() != s.human:
                return {"error": "not your turn"}
            result = s.submit(
                codec_id=int(codec_id) if codec_id is not None else None,
                discard=discard,
                confirm=confirm,
            )
            if result.get("illegal"):
                v = s.view()
                v["illegal"] = True
                return v
            if "coach" in result:
                v = s.view()
                v["coach"] = result["coach"]
                return v
            s.advance()
            return s.view()
