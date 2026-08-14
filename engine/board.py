"""Board: terrain / number-token / port assignment over the fixed topology."""
from __future__ import annotations

from dataclasses import dataclass

from .topology import TOPOLOGY
from .types import PortType, Terrain


@dataclass
class Board:
    terrain: list[Terrain]           # per hex id
    numbers: list[int | None]        # per hex id; None on the desert
    ports: dict[int, PortType]       # port-slot edge id -> port type (9 entries)

    @property
    def desert_hex(self) -> int:
        return self.terrain.index(Terrain.DESERT)

    def port_vertices(self, port_edge: int) -> tuple[int, int]:
        return TOPOLOGY.edge_vertices[port_edge]

    def hexes_with_number(self, number: int) -> list[int]:
        return [h for h, n in enumerate(self.numbers) if n == number]

    # --- canonical serialization (rules.md §4.3 step 6) ---

    def to_dict(self) -> dict:
        return {
            "terrain": [t.value for t in self.terrain],
            "numbers": list(self.numbers),
            "ports": {str(e): p.value for e, p in sorted(self.ports.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        return cls(
            terrain=[Terrain(t) for t in d["terrain"]],
            numbers=[n if n is None else int(n) for n in d["numbers"]],
            ports={int(e): PortType(p) for e, p in d["ports"].items()},
        )
