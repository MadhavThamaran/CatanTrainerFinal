"""Dice policies: IID baseline, Balanced Dice (behavioral spec:
docs/balanced_die_rules.md), and a scripted policy for tests.

Interface note: the spec sketches `next_roll(public_state, latent_state,
active_player)`. The controllers here *are* the latent state (they live
inside GameState and are cloned with it), so the signature reduces to
`next_roll(active_player)`. No current policy needs public state; the wider
signature can be restored when a policy does.

All randomness comes from an owned `random.Random`, so rolls are
deterministic under a fixed seed and replayable via `clone()`.
"""
from __future__ import annotations

import random
from collections import deque

# Constants from docs/balanced_die_rules.md §11.
MIN_CARDS_BEFORE_RESHUFFLE = 13
RECENT_ROLL_REDUCTION = 0.34
SEVEN_STREAK_REDUCTION = 0.4
RECENT_ROLL_MEMORY = 5

_ALL_PAIRS = [(a, b) for a in range(1, 7) for b in range(1, 7)]  # 36 ordered outcomes


class DicePolicy:
    """Stateful dice generator. Subclasses must be deterministic under seed."""

    def next_roll(self, active_player: int) -> int:
        raise NotImplementedError

    def clone(self) -> "DicePolicy":
        raise NotImplementedError

    def reseed(self, seed: int) -> None:
        """Replace the internal random stream WITHOUT touching structural
        latent state (deck composition, recent-roll memory, 7 tracking).
        Used by search determinization so sampled worlds draw independent
        chance outcomes from the same latent dice state."""
        raise NotImplementedError


class IIDDice(DicePolicy):
    """Plain 2d6 — debugging/ablation baseline (rules.md §13.3 v1)."""

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)

    def next_roll(self, active_player: int) -> int:
        return self._rng.randint(1, 6) + self._rng.randint(1, 6)

    def clone(self) -> "IIDDice":
        c = IIDDice()
        c._rng.setstate(self._rng.getstate())
        return c

    def reseed(self, seed: int) -> None:
        self._rng = random.Random(seed)


class ScriptedDice(DicePolicy):
    """Fixed sequence of totals — for tests. Repeats the last total when exhausted."""

    def __init__(self, totals: list[int]):
        self._totals = list(totals)
        self._i = 0

    def next_roll(self, active_player: int) -> int:
        t = self._totals[min(self._i, len(self._totals) - 1)]
        self._i += 1
        return t

    def clone(self) -> "ScriptedDice":
        c = ScriptedDice(self._totals)
        c._i = self._i
        return c

    def reseed(self, seed: int) -> None:
        pass  # scripted: nothing random to reseed


class BalancedDice(DicePolicy):
    """Balanced Dice per docs/balanced_die_rules.md.

    36-outcome deck drawn without replacement (reshuffle when < 13 cards
    remain), recent-roll suppression (0.34 per appearance in the last 5
    totals), and 7-balancing across players (streak term + fair-share term,
    final 7 multiplier clamped to [0, 2]).

    Interpretation choices flagged [To Calibrate] in PLAN.md §6:
      - the 7 streak resets on any non-7 roll;
      - "sevens against a player" = sevens rolled on that player's turn;
      - fair-share adjustment defaults to 1 until any 7 has been rolled.
    """

    def __init__(self, seed: int | None = None, num_players: int = 2):
        self._rng = random.Random(seed)
        self._num_players = num_players
        self._buckets: dict[int, list[tuple[int, int]]] = {}
        self._cards_left = 0
        self._recent: deque[int] = deque(maxlen=RECENT_ROLL_MEMORY)
        self._sevens_against: dict[int, int] = {}
        self._total_sevens = 0
        self._streak_player: int | None = None
        self._streak_count = 0
        self._reshuffle()

    # --- deck ---

    def _reshuffle(self) -> None:
        self._buckets = {t: [] for t in range(2, 13)}
        for pair in _ALL_PAIRS:
            self._buckets[sum(pair)].append(pair)
        self._cards_left = 36

    # --- weighting (spec §§3-9) ---

    def _weights(self, active_player: int) -> dict[int, float]:
        weights: dict[int, float] = {}
        for total, bucket in self._buckets.items():
            w = len(bucket) / self._cards_left  # §3 base weight
            w *= max(0.0, 1.0 - RECENT_ROLL_REDUCTION * self._recent.count(total))  # §4
            weights[total] = w

        # §§6-9: adjust only total 7.
        if self._num_players >= 2:
            if self._total_sevens > 0:
                ideal = 1.0 / self._num_players
                pct = self._sevens_against.get(active_player, 0) / self._total_sevens
                player_adj = 1.0 + (ideal - pct) / ideal
            else:
                player_adj = 1.0
            sign = -1.0 if self._streak_player == active_player else 1.0
            streak_adj = SEVEN_STREAK_REDUCTION * self._streak_count * sign
            seven_mult = min(2.0, max(0.0, player_adj + streak_adj))  # §9 clamp
            weights[7] *= seven_mult
        return weights

    # --- rolling (spec §10) ---

    def next_roll(self, active_player: int) -> int:
        self._sevens_against.setdefault(active_player, 0)
        if self._cards_left < MIN_CARDS_BEFORE_RESHUFFLE:
            self._reshuffle()

        weights = self._weights(active_player)
        mass = sum(weights.values())
        if mass <= 0.0:
            # Degenerate corner (every remaining total suppressed): fall back
            # to raw deck composition. Not covered by the spec.
            weights = {t: len(b) / self._cards_left for t, b in self._buckets.items()}
            mass = sum(weights.values())

        pick = self._rng.uniform(0.0, mass)
        cum = 0.0
        total = 12
        for t in range(2, 13):
            cum += weights[t]
            if pick <= cum:
                total = t
                break

        bucket = self._buckets[total]
        pair = bucket.pop(self._rng.randrange(len(bucket)))
        assert sum(pair) == total
        self._cards_left -= 1
        self._recent.append(total)

        if total == 7:
            self._sevens_against[active_player] += 1
            self._total_sevens += 1
            if self._streak_player == active_player:
                self._streak_count += 1
            else:
                self._streak_player = active_player
                self._streak_count = 1
        else:
            self._streak_player = None
            self._streak_count = 0
        return total

    def reseed(self, seed: int) -> None:
        self._rng = random.Random(seed)  # deck/memory/7-state untouched

    def clone(self) -> "BalancedDice":
        c = BalancedDice(num_players=self._num_players)
        c._rng.setstate(self._rng.getstate())
        c._buckets = {t: list(b) for t, b in self._buckets.items()}
        c._cards_left = self._cards_left
        c._recent = deque(self._recent, maxlen=RECENT_ROLL_MEMORY)
        c._sevens_against = dict(self._sevens_against)
        c._total_sevens = self._total_sevens
        c._streak_player = self._streak_player
        c._streak_count = self._streak_count
        return c
