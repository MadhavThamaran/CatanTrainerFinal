"""Trainer service (M7): puzzle presentation and submission scoring.

Pure logic, no HTTP — `server.py` is a thin shell over this, and tests
drive it directly. Serves the OBSERVATION-level view of each puzzle (the
stored perfect state stays server-side) and never leaks Q-values, points,
or the best move in the presentation payload.
"""
from __future__ import annotations

import random

from engine import Building, DevCard, GameState, Phase, Resource
from puzzles import load_puzzles, score_move

from .actions import describe_move
from .elo import Ratings
from .layout import LAYOUT

_PROMPTS = {
    "placement": "Setup draft: place your settlement.",
    "robber": "A robber move is due: pick the best hex.",
    "endgame": "The race is on: find the best move.",
    "devcard": "Development card in hand: find the best move.",
    "trade": "Trade routes open: find the best move.",
    "midgame": "Find the best move.",
}


class TrainerService:
    def __init__(self, puzzle_path: str, state_path: str, seed: int = 0):
        self.puzzles = load_puzzles(puzzle_path)
        if not self.puzzles:
            raise ValueError(f"no puzzles in {puzzle_path}")
        self._by_id = {p.id: p for p in self.puzzles}
        self.ratings = Ratings(state_path)
        self._rng = random.Random(seed)
        self._last_id: str | None = None

    # --- presentation ---

    def next_puzzle(self) -> dict:
        pool = [p for p in self.puzzles if p.id != self._last_id] or self.puzzles
        puzzle = self.ratings.pick(pool, self._rng)
        self._last_id = puzzle.id
        return self.present(puzzle.id)

    def present(self, puzzle_id: str) -> dict:
        p = self._by_id[puzzle_id]
        state = GameState.from_dict(p.state)
        actor = p.actor
        entry = self.ratings.puzzle_entry(p.id, p.difficulty)
        return {
            "puzzle_id": p.id,
            "phase": p.phase,
            "difficulty": p.difficulty,
            "prompt": self._prompt(p, state),
            "user_rating": round(self.ratings.user, 1),
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

    def submit(
        self, puzzle_id: str, codec_id: int, road_codec_id: int | None = None
    ) -> dict:
        p = self._by_id[puzzle_id]
        if not any(m.codec_id == codec_id for m in p.moves):
            # Composed an action that isn't legal here — reject softly so the
            # user can try another; not scored, not rated.
            return {"illegal": True}
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
                    return {"illegal": True}
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
        rating = self.ratings.record(
            p.id,
            p.difficulty,
            rated_points,
            phase=p.phase,
            regret=round(p.moves[0].q - chosen_q, 4),
        )

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
        # Human explanation built from board-notation labels at serve time
        # (the stored explanation uses raw engine reprs — unreadable).
        best, second = table[0], table[1]
        explanation = (
            f"Best: {best['label']} — {p.moves[0].q:.0%} win chance, "
            f"{p.gap:.0%} ahead of {second['label']}."
        )
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
            "best_codec_id": p.best_codec_id,
            "gap": p.gap,
            "explanation": explanation,
            "table": table,
            "road": road,
            "rating": rating,
        }


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
