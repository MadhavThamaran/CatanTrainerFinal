"""Board graph invariants (rules.md §3.1)."""
from engine import TOPOLOGY


def test_entity_counts():
    assert TOPOLOGY.num_hexes == 19
    assert TOPOLOGY.num_vertices == 54
    assert TOPOLOGY.num_edges == 72
    assert len(TOPOLOGY.coastal_ring) == 30
    assert len(TOPOLOGY.port_slot_edges) == 9


def test_local_degrees():
    for h in range(TOPOLOGY.num_hexes):
        assert len(TOPOLOGY.hex_vertices[h]) == 6
        assert len(TOPOLOGY.hex_edges[h]) == 6
        assert 2 <= len(TOPOLOGY.hex_neighbors[h]) <= 6
    for v in range(TOPOLOGY.num_vertices):
        assert 2 <= len(TOPOLOGY.vertex_edges[v]) <= 3
        assert 1 <= len(TOPOLOGY.vertex_hexes[v]) <= 3
    for e in range(TOPOLOGY.num_edges):
        assert 1 <= len(TOPOLOGY.edge_hexes[e]) <= 2


def test_coastal_ring_is_closed_walk():
    ring = TOPOLOGY.coastal_ring
    assert len(set(ring)) == 30
    for i, e in enumerate(ring):
        assert len(TOPOLOGY.edge_hexes[e]) == 1  # coastal
        nxt = ring[(i + 1) % 30]
        shared = set(TOPOLOGY.edge_vertices[e]) & set(TOPOLOGY.edge_vertices[nxt])
        assert len(shared) == 1  # consecutive ring edges share one vertex


def test_ports_touch_two_coastal_vertices_each_and_never_share():
    seen: set[int] = set()
    for a, b in TOPOLOGY.port_slot_vertices:
        assert a != b
        assert not {a, b} & seen  # no vertex belongs to two ports
        seen.update((a, b))
    assert len(seen) == 18
