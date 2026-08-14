"""Game state: the perfect (omniscient) view plus per-player observations.

`GameState` is the perfect state used for simulation and search
(rules.md §14 `perfect_state_for_simulation`); `GameState.observation(p)`
is the player-visible view (`observation_state_for_player`). State is
mutable with an explicit `clone()` — search copies then descends.

The `Board` is treated as immutable after generation and is shared, not
copied, by `clone()`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .board import Board
from .dice import DicePolicy
from .topology import TOPOLOGY
from .types import (
    MAX_CITIES,
    MAX_ROADS,
    MAX_SETTLEMENTS,
    Building,
    DevCard,
    Phase,
    PortType,
    Resource,
)

# Snake draft A -> B -> B -> A (rules.md §5.1); index = setup placement number.
SETUP_ORDER = [0, 1, 1, 0]


@dataclass
class PlayerState:
    resources: dict[Resource, int] = field(
        default_factory=lambda: {r: 0 for r in Resource}
    )
    dev_cards: dict[DevCard, int] = field(
        default_factory=lambda: {c: 0 for c in DevCard}
    )
    # Cards bought this turn are unplayable until next turn (rules.md §7.4).
    dev_bought_this_turn: dict[DevCard, int] = field(
        default_factory=lambda: {c: 0 for c in DevCard}
    )
    knights_played: int = 0
    roads_left: int = MAX_ROADS
    settlements_left: int = MAX_SETTLEMENTS
    cities_left: int = MAX_CITIES

    def hand_size(self) -> int:
        return sum(self.resources.values())

    def playable_dev_count(self, card: DevCard) -> int:
        return self.dev_cards[card] - self.dev_bought_this_turn[card]

    def clone(self) -> "PlayerState":
        return PlayerState(
            resources=dict(self.resources),
            dev_cards=dict(self.dev_cards),
            dev_bought_this_turn=dict(self.dev_bought_this_turn),
            knights_played=self.knights_played,
            roads_left=self.roads_left,
            settlements_left=self.settlements_left,
            cities_left=self.cities_left,
        )


@dataclass
class GameState:
    board: Board
    players: list[PlayerState]
    dice: DicePolicy
    rng: random.Random                      # steals & any future in-rules randomness
    dev_deck: list[DevCard]                 # top of deck = end of list
    robber_hex: int
    buildings: dict[int, tuple[int, Building]] = field(default_factory=dict)  # vertex -> (player, kind)
    roads: dict[int, int] = field(default_factory=dict)                        # edge -> player

    phase: Phase = Phase.SETUP
    current_player: int = 0
    turn_count: int = 0

    # setup-phase cursor
    setup_index: int = 0                    # 0..3 into SETUP_ORDER
    awaiting_setup_road: bool = False
    last_setup_settlement: int | None = None

    # per-turn flags / pending forced sub-decisions
    needs_roll: bool = True
    last_roll: int | None = None
    pending_discards: list[int] = field(default_factory=list)  # player ids, in order
    pending_robber: bool = False
    free_roads: int = 0                     # Road Building placements remaining
    dev_played_this_turn: bool = False

    longest_road_holder: int | None = None
    largest_army_holder: int | None = None
    winner: int | None = None

    # --- whose decision is it ---

    def player_to_act(self) -> int:
        if self.phase is Phase.SETUP:
            return SETUP_ORDER[self.setup_index]
        if self.pending_discards:
            return self.pending_discards[0]
        return self.current_player

    # --- victory points (rules.md §9.2 visible vs total) ---

    def visible_vp(self, player: int) -> int:
        vp = 0
        for owner, kind in self.buildings.values():
            if owner == player:
                vp += 1 if kind is Building.SETTLEMENT else 2
        if self.longest_road_holder == player:
            vp += 2
        if self.largest_army_holder == player:
            vp += 2
        return vp

    def total_vp(self, player: int) -> int:
        # Hidden VP dev cards count toward the win immediately (rules.md §8.5)
        # but never toward visible VP.
        return self.visible_vp(player) + self.players[player].dev_cards[DevCard.VICTORY_POINT]

    # --- ports & trade ratios (rules.md §10) ---

    def owned_ports(self, player: int) -> set[PortType]:
        owned: set[PortType] = set()
        for edge, port in self.board.ports.items():
            for v in TOPOLOGY.edge_vertices[edge]:
                b = self.buildings.get(v)
                if b is not None and b[0] == player:
                    owned.add(port)
        return owned

    def trade_ratio(self, player: int, give: Resource) -> int:
        ports = self.owned_ports(player)
        if any(p.resource is give for p in ports):
            return 2
        if PortType.GENERIC in ports:
            return 3
        return 4

    # --- copying ---

    def clone(self) -> "GameState":
        rng = random.Random()
        rng.setstate(self.rng.getstate())
        return GameState(
            board=self.board,  # immutable after generation; shared
            players=[p.clone() for p in self.players],
            dice=self.dice.clone(),
            rng=rng,
            dev_deck=list(self.dev_deck),
            robber_hex=self.robber_hex,
            buildings=dict(self.buildings),
            roads=dict(self.roads),
            phase=self.phase,
            current_player=self.current_player,
            turn_count=self.turn_count,
            setup_index=self.setup_index,
            awaiting_setup_road=self.awaiting_setup_road,
            last_setup_settlement=self.last_setup_settlement,
            needs_roll=self.needs_roll,
            last_roll=self.last_roll,
            pending_discards=list(self.pending_discards),
            pending_robber=self.pending_robber,
            free_roads=self.free_roads,
            dev_played_this_turn=self.dev_played_this_turn,
            longest_road_holder=self.longest_road_holder,
            largest_army_holder=self.largest_army_holder,
            winner=self.winner,
        )

    # --- serialization ---

    def to_dict(self) -> dict:
        """Perfect-state serialization.

        Dice-controller and rng internals are not serialized — they are
        hidden latent state, so `from_dict` reconstructs an information-set-
        equivalent game with fresh seeded controllers. Exact replay of an
        original game remains (seed + action log).
        """
        return {
            "board": self.board.to_dict(),
            "players": [
                {
                    "resources": {r.value: n for r, n in p.resources.items()},
                    "dev_cards": {c.value: n for c, n in p.dev_cards.items()},
                    "dev_bought_this_turn": {
                        c.value: n for c, n in p.dev_bought_this_turn.items()
                    },
                    "knights_played": p.knights_played,
                    "roads_left": p.roads_left,
                    "settlements_left": p.settlements_left,
                    "cities_left": p.cities_left,
                }
                for p in self.players
            ],
            "dev_deck": [c.value for c in self.dev_deck],
            "robber_hex": self.robber_hex,
            "buildings": {
                str(v): [owner, kind.name] for v, (owner, kind) in sorted(self.buildings.items())
            },
            "roads": {str(e): p for e, p in sorted(self.roads.items())},
            "phase": self.phase.name,
            "current_player": self.current_player,
            "turn_count": self.turn_count,
            "setup_index": self.setup_index,
            "awaiting_setup_road": self.awaiting_setup_road,
            "last_setup_settlement": self.last_setup_settlement,
            "needs_roll": self.needs_roll,
            "last_roll": self.last_roll,
            "pending_discards": list(self.pending_discards),
            "pending_robber": self.pending_robber,
            "free_roads": self.free_roads,
            "dev_played_this_turn": self.dev_played_this_turn,
            "longest_road_holder": self.longest_road_holder,
            "largest_army_holder": self.largest_army_holder,
            "winner": self.winner,
        }

    @classmethod
    def from_dict(cls, d: dict, seed: int = 0) -> "GameState":
        """Reconstruct a game from `to_dict` output.

        Dice/rng latent state is NOT stored (it is hidden information), so
        the reconstruction gets fresh seeded controllers — an information-
        set-equivalent position, which is exactly what puzzle presentation
        and re-labeling need. Standard-topology boards only.
        """
        from .dice import BalancedDice

        players = []
        for p in d["players"]:
            players.append(
                PlayerState(
                    resources={Resource(k): int(n) for k, n in p["resources"].items()},
                    dev_cards={DevCard(k): int(n) for k, n in p["dev_cards"].items()},
                    dev_bought_this_turn={
                        DevCard(k): int(n)
                        for k, n in p["dev_bought_this_turn"].items()
                    },
                    knights_played=p["knights_played"],
                    roads_left=p["roads_left"],
                    settlements_left=p["settlements_left"],
                    cities_left=p["cities_left"],
                )
            )
        rng = random.Random(seed)
        return cls(
            board=Board.from_dict(d["board"]),
            players=players,
            dice=BalancedDice(seed=rng.randrange(2**63), num_players=len(players)),
            rng=rng,
            dev_deck=[DevCard(c) for c in d["dev_deck"]],
            robber_hex=d["robber_hex"],
            buildings={
                int(v): (owner, Building[kind])
                for v, (owner, kind) in d["buildings"].items()
            },
            roads={int(e): p for e, p in d["roads"].items()},
            phase=Phase[d["phase"]],
            current_player=d["current_player"],
            turn_count=d["turn_count"],
            setup_index=d["setup_index"],
            awaiting_setup_road=d["awaiting_setup_road"],
            last_setup_settlement=d.get("last_setup_settlement"),
            needs_roll=d["needs_roll"],
            last_roll=d["last_roll"],
            pending_discards=list(d["pending_discards"]),
            pending_robber=d["pending_robber"],
            free_roads=d["free_roads"],
            dev_played_this_turn=d["dev_played_this_turn"],
            longest_road_holder=d["longest_road_holder"],
            largest_army_holder=d["largest_army_holder"],
            winner=d["winner"],
        )

    def observation(self, viewer: int) -> dict:
        """Player-visible view (rules.md §14): hides the opponent's hand
        composition and unplayed dev cards (counts only), the dev-deck
        order, and all dice-controller internals."""
        opp = 1 - viewer
        d = self.to_dict()
        del d["dev_deck"]
        d["dev_deck_count"] = len(self.dev_deck)
        me, other = self.players[viewer], self.players[opp]
        d["players"] = {
            "viewer": viewer,
            "own": {
                "resources": {r.value: n for r, n in me.resources.items()},
                "dev_cards": {c.value: n for c, n in me.dev_cards.items()},
                "dev_bought_this_turn": {
                    c.value: n for c, n in me.dev_bought_this_turn.items()
                },
                "knights_played": me.knights_played,
            },
            "opponent": {
                "hand_size": other.hand_size(),
                "dev_card_count": sum(other.dev_cards.values()),
                "knights_played": other.knights_played,
                "visible_vp": self.visible_vp(opp),
            },
        }
        return d
