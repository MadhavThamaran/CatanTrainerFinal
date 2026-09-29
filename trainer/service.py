"""Trainer service (M7): puzzle presentation and submission scoring.

Pure logic, no HTTP — `server.py` is a thin shell over this, and tests
drive it directly. Serves the OBSERVATION-level view of each puzzle (the
stored perfect state stays server-side) and never leaks Q-values, points,
or the best move in the presentation payload.
"""
from __future__ import annotations

import random
import time

from engine import Building, DevCard, GameState, Phase, Resource, legal_actions
from net.codec import encode_action
from puzzles import load_puzzles, score_move
from puzzles.explain import move_facts, render, render_miss

from . import dashboard as dashboard_module
from . import srs
from .actions import describe_move
from .elo import Ratings
from .layout import LAYOUT

_DASHBOARD_TTL = 30.0

_PROMPTS = {
    "placement": "Setup draft: place your settlement.",
    "robber": "A robber move is due: pick the best hex.",
    "endgame": "The race is on: find the best move.",
    "devcard": "Development card in hand: find the best move.",
    "trade": "Trade routes open: find the best move.",
    "midgame": "Find the best move.",
}


class TrainerService:
    def __init__(self, puzzle_path: str, store, seed: int = 0):
        self.puzzles = load_puzzles(puzzle_path)
        if not self.puzzles:
            raise ValueError(f"no puzzles in {puzzle_path}")
        self._by_id = {p.id: p for p in self.puzzles}
        self.store = store
        self._rng = random.Random(seed)
        self._last_id: dict[int, str] = {}
        self._ratings_cache: dict[int, Ratings] = {}
        self._dashboard_cache: dict[int, tuple[float, dict]] = {}

    def ratings_for(self, user_id: int) -> Ratings:
        r = self._ratings_cache.get(user_id)
        if r is None:
            r = self._ratings_cache[user_id] = Ratings(self.store, user_id)
        return r

    # --- presentation ---

    def dashboard(self, user_id: int) -> dict:
        cached = self._dashboard_cache.get(user_id)
        now = time.time()
        if cached is not None and now - cached[0] < _DASHBOARD_TTL:
            return cached[1]
        payload = dashboard_module.build(self.ratings_for(user_id))
        self._dashboard_cache[user_id] = (now, payload)
        return payload

    def next_puzzle(self, user_id: int, phase: str | None = None) -> dict:
        ratings = self.ratings_for(user_id)
        last = self._last_id.get(user_id)
        pool = [p for p in self.puzzles if p.id != last] or self.puzzles
        puzzle = ratings.pick(pool, self._rng, phase=phase)
        self._last_id[user_id] = puzzle.id
        return self.present(puzzle.id, user_id)

    def present(self, puzzle_id: str, user_id: int) -> dict:
        p = self._by_id[puzzle_id]
        state = GameState.from_dict(p.state)
        actor = p.actor
        ratings = self.ratings_for(user_id)
        entry = ratings.puzzle_entry(p.id, p.difficulty)
        return {
            "puzzle_id": p.id,
            "phase": p.phase,
            "difficulty": p.difficulty,
            "prompt": self._prompt(p, state),
            "user_rating": round(ratings.user, 1),
            "puzzle_rating": round(entry["rating"], 1),
            "layout": LAYOUT,
            "board": self._board_state(state, actor),
            "context": self._context(state, actor),
            # Canonical codec-id order: puzzle moves are stored RANKED, and
            # shipping them ranked would leak the answer as "first option".
            "moves": sorted(
                (describe_move(m.codec_id, state, actor) for m in p.moves),
                key=lambda d: d["codec_id"],
            ),
        }

    def _prompt(self, p, state: GameState) -> str:
        if state.free_roads > 0:
            return "Road Building: place your free road."
        return _PROMPTS.get(p.phase, _PROMPTS["midgame"])

    def _board_state(self, state: GameState, actor: int) -> dict:
        return board_state(state, actor)

    def _context(self, state: GameState, actor: int) -> dict:
        return context(state, actor)

    # --- submission ---

    def _explain(self, p, state: GameState, codec_id: int, best: dict, second: dict) -> str:
        """EXPLAIN_SPEC: facts-grounded lead clause (stored `p.facts` when
        the puzzle was annotated, else computed on the fly — same cheap,
        no-search functions either way), falling back to the win-prob line
        when nothing about the move clears salience; plus a "yours missed
        X" sentence when `codec_id` isn't the best move."""
        if p.facts is not None:
            facts_best, facts_second = p.facts["best"], p.facts["second"]
        else:
            actions = legal_actions(state)
            by_codec = {encode_action(a): a for a in actions if encode_action(a) is not None}
            facts_best = move_facts(state, by_codec[p.best_codec_id], p.actor)
            facts_second = move_facts(state, by_codec[p.moves[1].codec_id], p.actor)

        text = render(facts_best, facts_second, p.phase)
        if text is None:
            text = (
                f"Best: {best['label']} — {p.moves[0].q:.0%} win chance, "
                f"{p.gap:.0%} ahead of {second['label']}."
            )
        if codec_id != p.best_codec_id:
            actions = legal_actions(state)
            chosen_action = next(
                a for a in actions if encode_action(a) == codec_id
            )
            facts_chosen = move_facts(state, chosen_action, p.actor)
            miss = render_miss(facts_chosen, facts_best)
            if miss:
                text += " " + miss
        return text

    def _score(self, p, codec_id: int, road_codec_id: int | None) -> dict | None:
        """Points/table/explanation shared by the rated and SRS submit
        paths — scoring is identical either way; only what happens to the
        rating differs. Returns None for an illegal codec_id."""
        if not any(m.codec_id == codec_id for m in p.moves):
            return None
        points = score_move(p, codec_id)
        state = GameState.from_dict(p.state)

        # Placement composite: the road stage is scored only along the
        # engine's line (chess-style — the follow-up exists for the best
        # settlement); otherwise it is informational.
        road = None
        rated_points = points
        if p.followup is not None and road_codec_id is not None:
            fu = p.followup
            if codec_id == fu["parent_codec_id"]:
                fm = next(
                    (m for m in fu["moves"] if m["codec_id"] == road_codec_id), None
                )
                if fm is None:
                    return None
                road = {
                    "scored": True,
                    "points": fm["points"],
                    "best_codec_id": fu["best_codec_id"],
                    "gap": fu["gap"],
                    "table": [
                        {
                            **describe_move(m["codec_id"], state, p.actor),
                            "q": m["q"],
                            "points": m["points"],
                            "rank": m["rank"],
                            "chosen": m["codec_id"] == road_codec_id,
                            "best": m["codec_id"] == fu["best_codec_id"],
                        }
                        for m in fu["moves"]
                    ],
                }
                rated_points = round((points + fm["points"]) / 2)
            else:
                road = {"scored": False}
        chosen_q = next(m.q for m in p.moves if m.codec_id == codec_id)
        regret = round(p.moves[0].q - chosen_q, 4)

        table = []
        for m in p.moves:
            desc = describe_move(m.codec_id, state, p.actor)
            table.append(
                {
                    **desc,
                    "q": m.q,
                    "points": m.points,
                    "rank": m.rank,
                    "chosen": m.codec_id == codec_id,
                    "best": m.codec_id == p.best_codec_id,
                }
            )
        # EXPLAIN_SPEC: a facts-grounded clause when available (a "why",
        # not just the win-prob edge), falling back to the win-prob line
        # for puzzles not yet annotated; plus one sentence on what the
        # user's own move missed, when it wasn't the best one.
        best, second = table[0], table[1]
        explanation = self._explain(p, state, codec_id, best, second)
        if road is not None and road.get("scored"):
            road_best = next(m for m in road["table"] if m["best"])
            explanation += (
                f" Then: {road_best['label']}"
                + (" — you found it." if road["points"] == 100 else ".")
            )
        elif road is not None:
            explanation += " (Road not scored — the engine line starts from a different settlement.)"
        return {
            "points": points,
            "rated_points": rated_points,
            "regret": regret,
            "best_codec_id": p.best_codec_id,
            "gap": p.gap,
            "explanation": explanation,
            "table": table,
            "road": road,
        }

    def submit(
        self,
        puzzle_id: str,
        codec_id: int,
        user_id: int,
        road_codec_id: int | None = None,
    ) -> dict:
        ratings = self.ratings_for(user_id)
        p = self._by_id[puzzle_id]
        scored = self._score(p, codec_id, road_codec_id)
        if scored is None:
            # Composed an action that isn't legal here — reject softly so the
            # user can try another; not scored, not rated.
            return {"illegal": True}
        rating = ratings.record(
            p.id, p.difficulty, scored["rated_points"],
            phase=p.phase, regret=scored["regret"],
        )
        # SRS entry point (SRS_SPEC §3): only the RATED trainer path
        # enqueues a miss — lesson/lab/SRS-review attempts never do.
        if rating["rated"] and scored["rated_points"] < srs.PASS_THRESHOLD:
            srs.add(ratings.srs, p.id)
            ratings.save()
        return {**scored, "rating": rating}

    def submit_srs(
        self,
        puzzle_id: str,
        codec_id: int,
        user_id: int,
        road_codec_id: int | None = None,
    ) -> dict:
        """SRS review submission: scored identically to `submit` for
        DISPLAY, but never touches puzzle-Elo (SRS_SPEC §1/§3) — only the
        item's box moves."""
        ratings = self.ratings_for(user_id)
        p = self._by_id[puzzle_id]
        if p.id not in ratings.srs:
            return {"error": "puzzle is not in your review queue"}
        scored = self._score(p, codec_id, road_codec_id)
        if scored is None:
            return {"illegal": True}
        passed = scored["rated_points"] >= srs.PASS_THRESHOLD
        srs_result = srs.record_review(ratings.srs, p.id, passed)
        ratings.save()
        return {**scored, "srs_result": srs_result}

    # --- SRS (spaced repetition on missed puzzles) ---

    def next_srs(self, user_id: int) -> dict | None:
        ratings = self.ratings_for(user_id)
        due = srs.due_ids(ratings.srs)
        if not due:
            return None
        payload = self.present(due[0], user_id)
        payload["srs"] = True
        payload["srs_queue"] = len(due)
        return payload

    def srs_summary(self, user_id: int) -> dict:
        return srs.summary(self.ratings_for(user_id).srs)


# --- shared presentation helpers (trainer + play mode) ---


def board_state(state: GameState, actor: int) -> dict:
    """Dynamic board content; actor is always 'you'."""
    board = state.board
    hexes = [
        {
            "id": h,
            "terrain": board.terrain[h].value,
            "number": board.numbers[h],
            "robber": state.robber_hex == h,
        }
        for h in range(19)
    ]
    ports = [
        {"v1": a, "v2": b, "type": port.value}
        for edge, port in board.ports.items()
        for a, b in [board.port_vertices(edge)]
    ]
    buildings = {
        str(v): {
            "mine": owner == actor,
            "kind": "city" if kind is Building.CITY else "settlement",
        }
        for v, (owner, kind) in state.buildings.items()
    }
    roads = {str(e): {"mine": owner == actor} for e, owner in state.roads.items()}
    return {"hexes": hexes, "ports": ports, "buildings": buildings, "roads": roads}


def context(state: GameState, actor: int) -> dict:
    opp = 1 - actor
    me = state.players[actor]
    setup = state.phase is Phase.SETUP
    return {
        "resources": {r.value: me.resources[r] for r in Resource},
        "dev_cards": {
            c.value: me.dev_cards[c]
            for c in DevCard
            if me.dev_cards[c] > 0
        },
        "my_vp": state.total_vp(actor),
        "opp_visible_vp": state.visible_vp(opp),
        "opp_hand_size": state.players[opp].hand_size(),
        "opp_dev_count": sum(state.players[opp].dev_cards.values()),
        "my_knights": me.knights_played,
        "opp_knights": state.players[opp].knights_played,
        "longest_road": (
            "you" if state.longest_road_holder == actor
            else "opp" if state.longest_road_holder == opp else None
        ),
        "largest_army": (
            "you" if state.largest_army_holder == actor
            else "opp" if state.largest_army_holder == opp else None
        ),
        "deck_count": len(state.dev_deck),
        "last_roll": state.last_roll,
        "setup": setup,
    }
