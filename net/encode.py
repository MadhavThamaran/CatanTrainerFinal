"""State -> feature vector for the policy/value net (PLAN.md Stage 5).

Encoded from the PERSPECTIVE OF THE PLAYER TO ACT ("me"/"opp"), so the value
head predicts P(actor wins) and one network serves both seats. Encodes the
*perfect* (determinized) state — the net replaces the static evaluator and
heuristic priors inside the search, which operates on determinized worlds.

Relational features (M5 fix #3): the gen-1/2 nets underexpanded because
"which road opens which spot" is a multi-hop graph pattern an MLP cannot
learn from flat occupancy bits at laptop data scale. We therefore inject
1-hop relational primitives — per-vertex placement value, placeability, and
network-adjacency flags, plus open-spot counts — so the net composes context
instead of rediscovering graph reachability.

The board section (terrain/numbers/ports/placement values) is static per
game and cached ON the Board object (attribute, not an id()-keyed dict —
ids can be reused across games in long-lived workers).

Layout offsets are exported as constants; net/symmetry.py builds its
permutations from them so the two can never drift.
"""
from __future__ import annotations

import numpy as np

from engine import Building, DevCard, GameState, Phase, PortType, Resource, TOPOLOGY
from engine.longest_road import longest_road_length
from engine.types import VP_TO_WIN

from agents.heuristic import PIP, placement_value

_TERRAINS = 6
_PORT_DIMS = 6  # generic + 5 resources
_RESOURCES = list(Resource)
_N_SCALARS = 37

# --- layout (board section: static per game) ---
O_TERRAIN = 0                                   # 19 x 6 one-hot
O_PIP = O_TERRAIN + 19 * _TERRAINS              # 19 (pips / 5)
O_PORTS = O_PIP + 19                            # 54 x 6 port type per vertex
O_PVALUE = O_PORTS + 54 * _PORT_DIMS            # 54 heuristic placement value
BOARD_DIM = O_PVALUE + 54

# --- layout (dynamic section) ---
O_ROBBER = BOARD_DIM                            # 19 one-hot
O_BUILDINGS = O_ROBBER + 19                     # 54 x 4 (me-s, me-c, opp-s, opp-c)
O_ROADS = O_BUILDINGS + 54 * 4                  # 72 x 2 (mine, theirs)
O_VFLAGS = O_ROADS + 72 * 2                     # 54 x 3 (placeable, my-net, opp-net)
O_SCALARS = O_VFLAGS + 54 * 3
FEATURE_DIM = O_SCALARS + _N_SCALARS
DYNAMIC_DIM = FEATURE_DIM - BOARD_DIM

_PORT_INDEX = {
    PortType.GENERIC: 0,
    PortType.WOOD: 1,
    PortType.BRICK: 2,
    PortType.SHEEP: 3,
    PortType.WHEAT: 4,
    PortType.ORE: 5,
}

_BOARD_CACHE_ATTR = "_net_board_features"


class StateEncoder:
    def encode(self, state: GameState, viewer: int) -> np.ndarray:
        return np.concatenate([_board(state), self._dynamic(state, viewer)])

    # --- dynamic section ---

    def _dynamic(self, state: GameState, me: int) -> np.ndarray:
        opp = 1 - me
        robber = np.zeros(19, dtype=np.float32)
        robber[state.robber_hex] = 1.0

        buildings = np.zeros((54, 4), dtype=np.float32)
        for v, (owner, kind) in state.buildings.items():
            col = (0 if owner == me else 2) + (0 if kind is Building.SETTLEMENT else 1)
            buildings[v, col] = 1.0

        roads = np.zeros((72, 2), dtype=np.float32)
        for e, owner in state.roads.items():
            roads[e, 0 if owner == me else 1] = 1.0

        # Relational vertex flags (M5 fix #3): "placeable ∧ near my network"
        # is the 1-hop composition of an expansion opportunity. Computed by
        # iterating the (few) buildings/roads instead of calling the rules
        # predicates per vertex — encoding was 24% of net-guided search time.
        # Must stay bit-equal to `_vertex_placeable` /
        # `_legal_settlement_vertices` (tests/test_net.py pins this).
        vflags = np.zeros((54, 3), dtype=np.float32)
        blocked = np.zeros(54, dtype=bool)
        my_touch = np.zeros(54, dtype=bool)
        opp_touch = np.zeros(54, dtype=bool)
        for v in state.buildings:
            blocked[v] = True
            for nb in TOPOLOGY.vertex_neighbors[v]:
                blocked[nb] = True
        vflags[:, 0] = ~blocked
        for v, (owner, _) in state.buildings.items():
            vflags[v, 1 if owner == me else 2] = 1.0
        for e, owner in state.roads.items():
            col = 1 if owner == me else 2
            touch = my_touch if owner == me else opp_touch
            for v in TOPOLOGY.edge_vertices[e]:
                vflags[v, col] = 1.0
                touch[v] = True

        my_spots = int(np.count_nonzero(~blocked & my_touch))
        opp_spots = int(np.count_nonzero(~blocked & opp_touch))

        my, op = state.players[me], state.players[opp]
        scalars = np.array(
            [
                *[min(my.resources[r], 8) / 8.0 for r in _RESOURCES],          # 5
                min(op.hand_size(), 12) / 12.0,                                 # 1
                *[min(my.dev_cards[c], 3) / 3.0 for c in DevCard],              # 5
                min(sum(my.dev_bought_this_turn.values()), 2) / 2.0,            # 1
                min(sum(op.dev_cards.values()), 5) / 5.0,                       # 1
                len(state.dev_deck) / 25.0,                                     # 1
                min(my.knights_played, 5) / 5.0,                                # 1
                min(op.knights_played, 5) / 5.0,                                # 1
                state.total_vp(me) / VP_TO_WIN,                                 # 1
                state.visible_vp(opp) / VP_TO_WIN,                              # 1
                float(state.longest_road_holder == me),                         # 1
                float(state.longest_road_holder == opp),                        # 1
                float(state.largest_army_holder == me),                         # 1
                float(state.largest_army_holder == opp),                        # 1
                min(longest_road_length(state, me), 15) / 15.0,                 # 1
                min(longest_road_length(state, opp), 15) / 15.0,                # 1
                my.roads_left / 15.0,                                           # 1
                my.settlements_left / 5.0,                                      # 1
                my.cities_left / 4.0,                                           # 1
                op.roads_left / 15.0,                                           # 1
                op.settlements_left / 5.0,                                      # 1
                op.cities_left / 4.0,                                           # 1
                float(state.phase is Phase.SETUP),                              # 1
                float(state.pending_robber),                                    # 1
                state.free_roads / 2.0,                                         # 1
                float(state.dev_played_this_turn),                              # 1
                float(state.needs_roll),                                        # 1
                min(my_spots, 6) / 6.0,                                         # 1
                min(opp_spots, 6) / 6.0,                                        # 1
            ],
            dtype=np.float32,
        )
        assert scalars.shape[0] == _N_SCALARS, scalars.shape
        return np.concatenate(
            [robber, buildings.ravel(), roads.ravel(), vflags.ravel(), scalars]
        )


def _board(state: GameState) -> np.ndarray:
    """Static-per-game board features, cached on the Board object itself
    (lifetime-tied: no id()-reuse collisions across games)."""
    board = state.board
    cached = getattr(board, _BOARD_CACHE_ATTR, None)
    if cached is not None:
        return cached
    terrain = np.zeros((19, _TERRAINS), dtype=np.float32)
    pip = np.zeros(19, dtype=np.float32)
    terrains = list(type(board.terrain[0]))  # Terrain members, stable order
    t_index = {t: i for i, t in enumerate(terrains)}
    for h in range(19):
        terrain[h, t_index[board.terrain[h]]] = 1.0
        n = board.numbers[h]
        if n is not None:
            pip[h] = PIP[n] / 5.0
    ports = np.zeros((54, _PORT_DIMS), dtype=np.float32)
    for edge, port in board.ports.items():
        for v in TOPOLOGY.edge_vertices[edge]:
            ports[v, _PORT_INDEX[port]] = 1.0
    pvalue = np.array(
        [placement_value(board, v) / 30.0 for v in range(54)], dtype=np.float32
    )
    vec = np.concatenate([terrain.ravel(), pip, ports.ravel(), pvalue])
    setattr(board, _BOARD_CACHE_ATTR, vec)
    return vec
