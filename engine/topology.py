"""Static board topology for the standard base board.

19 hexes / 54 vertices / 72 edges / 9 port slots on the coast (rules.md §3.1).

Hexes live on axial coordinates (q, r) with max(|q|, |r|, |q+r|) <= 2
(pointy-top, radius-2 hexagon). Corner positions are canonicalized on an
integer lattice: hex (q, r) has center (2q + r, 3r) and corner offsets
(0,±2), (±1,±1) — exact integer coordinates, so vertices dedupe without
floating point. All entities get dense integer ids, deterministic across
runs, so game state is plain arrays/dicts keyed by small ints.

Everything here is immutable and computed once at import (`TOPOLOGY`).
"""
from __future__ import annotations

from dataclasses import dataclass

# Clockwise corner offsets from a (2q + r, 3r) hex center. Consecutive
# entries (cyclically) are the endpoints of the hex's six edges.
_CORNER_OFFSETS = [(0, 2), (1, 1), (1, -1), (0, -2), (-1, -1), (-1, 1)]

# Axial neighbor directions (cube-coordinate adjacency).
_HEX_DIRECTIONS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]

# Port slot spacing around the 30-edge coastal ring: gaps sum to 30 and are
# all >= 2, so no two ports share a vertex (mirrors the official layout's
# spacing). [Engine Assumption] — positions fixed, types randomized
# (rules.md §4.3 step 4).
_PORT_RING_GAPS = [3, 3, 4, 3, 4, 3, 3, 4, 3]


@dataclass(frozen=True)
class Topology:
    hex_coords: list[tuple[int, int]]            # hex id -> axial (q, r)
    hex_vertices: list[list[int]]                # hex id -> 6 vertex ids (corner order)
    hex_edges: list[list[int]]                   # hex id -> 6 edge ids
    hex_neighbors: list[list[int]]               # hex id -> adjacent hex ids
    vertex_hexes: list[list[int]]                # vertex id -> adjacent hex ids (1..3)
    vertex_edges: list[list[int]]                # vertex id -> incident edge ids (2..3)
    vertex_neighbors: list[list[int]]            # vertex id -> adjacent vertex ids
    edge_vertices: list[tuple[int, int]]         # edge id -> (v_lo, v_hi)
    edge_hexes: list[list[int]]                  # edge id -> adjacent hex ids (1..2)
    coastal_ring: list[int]                      # 30 coastal edge ids in cyclic order
    port_slot_edges: list[int]                   # 9 coastal edge ids hosting ports
    port_slot_vertices: list[tuple[int, int]]    # per slot, the 2 coastal vertices

    @property
    def num_hexes(self) -> int:
        return len(self.hex_coords)

    @property
    def num_vertices(self) -> int:
        return len(self.vertex_hexes)

    @property
    def num_edges(self) -> int:
        return len(self.edge_vertices)

    def edge_between(self, v1: int, v2: int) -> int | None:
        for e in self.vertex_edges[v1]:
            if v2 in self.edge_vertices[e]:
                return e
        return None

    def edge_other_vertex(self, edge: int, vertex: int) -> int:
        a, b = self.edge_vertices[edge]
        return b if vertex == a else a


def _build() -> Topology:
    hex_coords = sorted(
        (q, r)
        for q in range(-2, 3)
        for r in range(-2, 3)
        if abs(q + r) <= 2
    )
    assert len(hex_coords) == 19
    hex_index = {c: i for i, c in enumerate(hex_coords)}

    # Corner lattice points per hex, in corner order.
    hex_corner_points: list[list[tuple[int, int]]] = []
    for q, r in hex_coords:
        cx, cy = 2 * q + r, 3 * r
        hex_corner_points.append([(cx + dx, cy + dy) for dx, dy in _CORNER_OFFSETS])

    vertex_points = sorted({p for corners in hex_corner_points for p in corners})
    assert len(vertex_points) == 54
    vertex_index = {p: i for i, p in enumerate(vertex_points)}

    hex_vertices = [[vertex_index[p] for p in corners] for corners in hex_corner_points]

    # Edges: consecutive corner pairs of each hex, deduped.
    edge_set: set[tuple[int, int]] = set()
    for verts in hex_vertices:
        for i in range(6):
            a, b = verts[i], verts[(i + 1) % 6]
            edge_set.add((min(a, b), max(a, b)))
    edge_vertices = sorted(edge_set)
    assert len(edge_vertices) == 72
    edge_index = {vv: i for i, vv in enumerate(edge_vertices)}

    hex_edges = [
        [edge_index[(min(verts[i], verts[(i + 1) % 6]), max(verts[i], verts[(i + 1) % 6]))]
         for i in range(6)]
        for verts in hex_vertices
    ]

    hex_neighbors = [
        [hex_index[(q + dq, r + dr)]
         for dq, dr in _HEX_DIRECTIONS if (q + dq, r + dr) in hex_index]
        for q, r in hex_coords
    ]

    vertex_hexes: list[list[int]] = [[] for _ in vertex_points]
    for h, verts in enumerate(hex_vertices):
        for v in verts:
            vertex_hexes[v].append(h)

    vertex_edges: list[list[int]] = [[] for _ in vertex_points]
    vertex_neighbors: list[list[int]] = [[] for _ in vertex_points]
    edge_hexes: list[list[int]] = [[] for _ in edge_vertices]
    for e, (a, b) in enumerate(edge_vertices):
        vertex_edges[a].append(e)
        vertex_edges[b].append(e)
        vertex_neighbors[a].append(b)
        vertex_neighbors[b].append(a)
    for h, edges in enumerate(hex_edges):
        for e in edges:
            edge_hexes[e].append(h)

    # Coastal ring: edges touching exactly one hex, walked in cyclic order.
    coastal = [e for e, hs in enumerate(edge_hexes) if len(hs) == 1]
    assert len(coastal) == 30
    coastal_set = set(coastal)
    ring = [min(coastal)]
    # Walk from the deterministic start edge toward its higher-id endpoint.
    v = max(edge_vertices[ring[0]])
    while len(ring) < 30:
        nxt = next(e for e in vertex_edges[v] if e in coastal_set and e != ring[-1])
        ring.append(nxt)
        v = _other(edge_vertices[nxt], v)
    assert _other(edge_vertices[ring[-1]], v) != v  # closed walk sanity

    slots: list[int] = []
    pos = 0
    for gap in _PORT_RING_GAPS:
        slots.append(ring[pos % 30])
        pos += gap
    assert len(set(slots)) == 9
    port_slot_vertices = [edge_vertices[e] for e in slots]

    return Topology(
        hex_coords=hex_coords,
        hex_vertices=hex_vertices,
        hex_edges=hex_edges,
        hex_neighbors=hex_neighbors,
        vertex_hexes=vertex_hexes,
        vertex_edges=vertex_edges,
        vertex_neighbors=vertex_neighbors,
        edge_vertices=edge_vertices,
        edge_hexes=edge_hexes,
        coastal_ring=ring,
        port_slot_edges=slots,
        port_slot_vertices=port_slot_vertices,
    )


def _other(pair: tuple[int, int], v: int) -> int:
    a, b = pair
    return b if v == a else a


TOPOLOGY = _build()
