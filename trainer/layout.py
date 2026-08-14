"""Board pixel layout (M7).

Computes fixed pixel coordinates for every hex, vertex, and edge from the
engine topology, so the web client does zero geometry — it draws polygons
through the vertex ids it is given. Pointy-top orientation, matching the
canonical lattice used by `engine.topology`:

    true_x = sqrt(3)/2 * lattice_x,   true_y = 1/2 * lattice_y
"""
from __future__ import annotations

import math

from engine import TOPOLOGY
from engine.topology import _CORNER_OFFSETS

_SCALE = 54.0
_SQ3_2 = math.sqrt(3.0) / 2.0


def _build() -> dict:
    vertex_points = sorted(
        {
            (2 * q + r + dx, 3 * r + dy)
            for q, r in TOPOLOGY.hex_coords
            for dx, dy in _CORNER_OFFSETS
        }
    )
    vx = [round(_SCALE * _SQ3_2 * p[0], 2) for p in vertex_points]
    vy = [round(_SCALE * 0.5 * p[1], 2) for p in vertex_points]
    # Shift into a positive viewbox with a margin.
    margin = 60.0
    ox, oy = margin - min(vx), margin - min(vy)
    vertices = [
        {"id": v, "x": round(vx[v] + ox, 2), "y": round(vy[v] + oy, 2)}
        for v in range(TOPOLOGY.num_vertices)
    ]
    hexes = []
    for h, (q, r) in enumerate(TOPOLOGY.hex_coords):
        hexes.append(
            {
                "id": h,
                "x": round(_SCALE * _SQ3_2 * (2 * q + r) + ox, 2),
                "y": round(_SCALE * 0.5 * (3 * r) + oy, 2),
                "vertex_ids": list(TOPOLOGY.hex_vertices[h]),
            }
        )
    edges = [
        {"id": e, "v1": a, "v2": b}
        for e, (a, b) in enumerate(TOPOLOGY.edge_vertices)
    ]
    width = round(max(v["x"] for v in vertices) + margin, 2)
    height = round(max(v["y"] for v in vertices) + margin, 2)
    return {
        "hexes": hexes,
        "vertices": vertices,
        "edges": edges,
        "width": width,
        "height": height,
        "center": {"x": round(width / 2, 2), "y": round(height / 2, 2)},
    }


LAYOUT = _build()
