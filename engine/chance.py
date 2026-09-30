"""Force a specific chance outcome (ANALYSIS_SPEC §3) — the analysis
board's one Catan-specific superpower, "you choose the dice."

Each helper affects exactly the NEXT occurrence of its event and leaves
`state.dice`/`state.rng`'s future stream bit-identical to the unforced
case: forcing an outcome never perturbs any OTHER randomness, so a line
can fork at a forced roll/steal/draw and every UNFORCED branch downstream
still behaves exactly as normal play would.
"""
from __future__ import annotations

import random

from .dice import DicePolicy
from .state import GameState
from .types import DevCard, Resource


class _OneShotRoll(DicePolicy):
    """Returns `total` for exactly one call, then delegates to `inner`
    untouched — `inner`'s structural state (deck composition, recent-roll
    memory, seven-streak tracking for BalancedDice) is never consumed by
    the forced roll, so it resumes exactly where it would have been."""

    def __init__(self, inner: DicePolicy, total: int):
        self._inner = inner
        self._total = total
        self._used = False

    def next_roll(self, active_player: int) -> int:
        if not self._used:
            self._used = True
            return self._total
        return self._inner.next_roll(active_player)

    def clone(self) -> "_OneShotRoll":
        c = _OneShotRoll(self._inner.clone(), self._total)
        c._used = self._used
        return c

    def reseed(self, seed: int) -> None:
        self._inner.reseed(seed)


def force_next_roll(state: GameState, total: int) -> None:
    """The next `state.dice.next_roll()` call (i.e. the next ROLL action)
    returns `total` exactly once."""
    assert 2 <= total <= 12, total
    state.dice = _OneShotRoll(state.dice, total)


class _OneShotChoice(random.Random):
    """A `random.Random` whose first `.choice()` call returns `forced`
    (if present in the sequence) WITHOUT consuming any entropy, then
    behaves exactly like the wrapped stream for every call after."""

    def __init__(self, forced):
        super().__init__()
        self._forced = forced
        self._used = False

    def choice(self, seq):
        if not self._used:
            self._used = True
            if self._forced in seq:
                return self._forced
        return super().choice(seq)


def force_next_steal(state: GameState, resource: Resource) -> None:
    """The next robber steal (`state.rng.choice` over the victim's hand,
    one entry per card held) returns `resource` if the victim holds any;
    otherwise the steal behaves normally (uniform over individual cards)."""
    rng = _OneShotChoice(resource)
    rng.setstate(state.rng.getstate())
    state.rng = rng


def force_next_draw(state: GameState, card: DevCard) -> None:
    """The next BUY_DEV_CARD draw pops `card` — moves its first remaining
    instance to the top of the deck (list order of every other card is
    unchanged, so this is a targeted move, not a reshuffle)."""
    deck = state.dev_deck
    idx = next((i for i in range(len(deck)) if deck[i] is card), None)
    if idx is None:
        raise ValueError(f"no {card} left in the deck")
    deck.append(deck.pop(idx))
