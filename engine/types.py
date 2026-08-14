"""Core enums and rule constants for the 1v1 Colonist-variant engine.

Authoritative ruleset: docs/rules.md and docs/1v1_Colonist_Inspired_Spec.md.
"""
from __future__ import annotations

from enum import Enum, auto


class Resource(Enum):
    WOOD = "wood"
    BRICK = "brick"
    SHEEP = "sheep"
    WHEAT = "wheat"
    ORE = "ore"


class Terrain(Enum):
    FOREST = "forest"        # wood
    HILLS = "hills"          # brick
    PASTURE = "pasture"      # sheep
    FIELDS = "fields"        # wheat
    MOUNTAINS = "mountains"  # ore
    DESERT = "desert"

    @property
    def resource(self) -> Resource | None:
        return _TERRAIN_RESOURCE[self]


_TERRAIN_RESOURCE = {
    Terrain.FOREST: Resource.WOOD,
    Terrain.HILLS: Resource.BRICK,
    Terrain.PASTURE: Resource.SHEEP,
    Terrain.FIELDS: Resource.WHEAT,
    Terrain.MOUNTAINS: Resource.ORE,
    Terrain.DESERT: None,
}


class DevCard(Enum):
    KNIGHT = "knight"
    VICTORY_POINT = "victory_point"
    ROAD_BUILDING = "road_building"
    YEAR_OF_PLENTY = "year_of_plenty"
    MONOPOLY = "monopoly"


class PortType(Enum):
    GENERIC = "3:1"  # 3:1 any resource
    WOOD = "wood"
    BRICK = "brick"
    SHEEP = "sheep"
    WHEAT = "wheat"
    ORE = "ore"

    @property
    def resource(self) -> Resource | None:
        return None if self is PortType.GENERIC else Resource(self.value)


class Building(Enum):
    SETTLEMENT = auto()
    CITY = auto()


class Phase(Enum):
    SETUP = auto()
    MAIN = auto()
    GAME_OVER = auto()


# --- Rule constants (spec references in comments) ---

NUM_PLAYERS = 2
VP_TO_WIN = 15                      # rules.md §2.1
DISCARD_THRESHOLD = 9               # discard when hand size > 9 (rules.md §2.2)
ROBBER_VISIBLE_VP_PROTECTION = 2    # protected while visible VP <= 2 (rules.md §9.2)

# Standard piece limits. [Engine Assumption]: spec is silent; standard CATAN values.
MAX_ROADS = 15
MAX_SETTLEMENTS = 5
MAX_CITIES = 4

LONGEST_ROAD_MIN = 5                # rules.md §11
LARGEST_ARMY_MIN = 3                # rules.md §12

# Terrain multiset (rules.md §3.2)
TERRAIN_COUNTS = {
    Terrain.FOREST: 4,
    Terrain.PASTURE: 4,
    Terrain.FIELDS: 4,
    Terrain.HILLS: 3,
    Terrain.MOUNTAINS: 3,
    Terrain.DESERT: 1,
}

# Number-token multiset (rules.md §3.3)
NUMBER_TOKENS = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]

# Port multiset (rules.md §3.4)
PORT_COUNTS = {
    PortType.GENERIC: 4,
    PortType.WOOD: 1,
    PortType.BRICK: 1,
    PortType.SHEEP: 1,
    PortType.WHEAT: 1,
    PortType.ORE: 1,
}

# Build costs (rules.md §7)
COST_ROAD = {Resource.WOOD: 1, Resource.BRICK: 1}
COST_SETTLEMENT = {
    Resource.WOOD: 1,
    Resource.BRICK: 1,
    Resource.SHEEP: 1,
    Resource.WHEAT: 1,
}
COST_CITY = {Resource.ORE: 3, Resource.WHEAT: 2}
COST_DEV_CARD = {Resource.ORE: 1, Resource.WHEAT: 1, Resource.SHEEP: 1}

# Development deck composition (rules.md §8)
DEV_DECK_COUNTS = {
    DevCard.KNIGHT: 14,
    DevCard.VICTORY_POINT: 5,
    DevCard.ROAD_BUILDING: 2,
    DevCard.YEAR_OF_PLENTY: 2,
    DevCard.MONOPOLY: 2,
}
