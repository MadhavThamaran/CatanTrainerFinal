"""1v1 Colonist-variant Catan engine (see PLAN.md Stage 1, docs/ for the ruleset).

Public API:
    new_game(seed) -> GameState
    legal_actions(state) -> list[Action]
    apply_action(state, action) -> None   (mutates; use state.clone() to branch)
"""
from .actions import Action, ActionType
from .board import Board
from .board_gen import generate_board, validate_board
from .chance import force_next_draw, force_next_roll, force_next_steal
from .dice import BalancedDice, DicePolicy, IIDDice, ScriptedDice
from .game import new_game
from .longest_road import longest_road_length
from .rules import apply_action, legal_actions, robber_destinations
from .state import GameState, PlayerState
from .topology import TOPOLOGY
from .types import Building, DevCard, Phase, PortType, Resource, Terrain

__all__ = [
    "Action",
    "ActionType",
    "BalancedDice",
    "Board",
    "Building",
    "DevCard",
    "DicePolicy",
    "GameState",
    "IIDDice",
    "Phase",
    "PlayerState",
    "PortType",
    "Resource",
    "ScriptedDice",
    "Terrain",
    "TOPOLOGY",
    "apply_action",
    "force_next_draw",
    "force_next_roll",
    "force_next_steal",
    "generate_board",
    "legal_actions",
    "longest_road_length",
    "new_game",
    "robber_destinations",
    "validate_board",
]
