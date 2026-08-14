"""Game construction: seeded, fully deterministic given (seed, action log)."""
from __future__ import annotations

import random

from .board import Board
from .board_gen import generate_board
from .dice import BalancedDice, DicePolicy
from .state import GameState, PlayerState
from .types import DEV_DECK_COUNTS, NUM_PLAYERS


def new_game(
    seed: int,
    board: Board | None = None,
    dice: DicePolicy | None = None,
) -> GameState:
    """Create a fresh game in the setup phase.

    All randomness (board, dev-deck order, dice, steals) derives from `seed`,
    so a game is exactly replayable from (seed, action log).
    """
    rng = random.Random(seed)
    if board is None:
        board = generate_board(rng)
    dev_deck = [c for c, n in DEV_DECK_COUNTS.items() for _ in range(n)]
    rng.shuffle(dev_deck)
    if dice is None:
        dice = BalancedDice(seed=rng.randrange(2**63), num_players=NUM_PLAYERS)
    return GameState(
        board=board,
        players=[PlayerState() for _ in range(NUM_PLAYERS)],
        dice=dice,
        rng=rng,
        dev_deck=dev_deck,
        robber_hex=board.desert_hex,  # robber starts on the desert (rules.md §4.3)
    )
