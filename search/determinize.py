"""Determinization: sample a complete world consistent with what the acting
player actually knows (PLAN.md §2.3).

Given a perfect state and a viewer, hide-and-resample the hidden components:

- **Opponent resource hand** — hand *size* is public; composition is drawn
  from the `CardTracker` belief when available (exact absent hidden
  discards), else from a production-weighted prior.
- **Opponent dev cards + deck order** — dev card *plays* are public events,
  so the pool `deck + opponent's held devs` has publicly known composition;
  we reshuffle that pool and re-deal the opponent's held count. The viewer's
  own cards are never touched.
- **Dice controller** — structural latent state (deck composition, recent
  memory, 7 tracking) is carried per PLAN §2.3; only the random *streams*
  are reseeded so different determinizations draw independent futures.

Everything public (board, buildings, roads, robber, VP, piece supplies,
viewer's hand and devs) is preserved exactly.
"""
from __future__ import annotations

import random

from engine import DevCard, GameState

from .belief import CardTracker, _production_weights, _weighted_pick


class Determinizer:
    def __init__(self, tracker: CardTracker | None = None, exact: bool = False):
        self._tracker = tracker
        # ANALYSIS_SPEC §4: analysis states are already perfect (both hands
        # revealed) — resampling hidden info there would REPLACE the true
        # opponent hand with a fake one. `exact=True` skips every hidden-info
        # swap below; only the chance streams get reseeded, so K "worlds"
        # differ in search randomness alone, degenerating cleanly to the
        # one true state.
        self._exact = exact

    def sample(self, state: GameState, viewer: int, rng: random.Random) -> GameState:
        det = state.clone()
        if not self._exact:
            opp = 1 - viewer
            opp_ps = det.players[opp]

            # --- opponent resource hand ---
            if self._tracker is not None and self._tracker.opp == opp:
                opp_ps.resources = self._tracker.sample_hand(state, rng)
            else:
                opp_ps.resources = _prior_hand(state, opp, rng)

            # --- opponent dev cards + deck: reshuffle the publicly-known pool ---
            pool = list(det.dev_deck)
            held = [c for c, n in opp_ps.dev_cards.items() for _ in range(n)]
            n_held = len(held)
            n_bought_now = sum(opp_ps.dev_bought_this_turn.values())
            pool += held
            rng.shuffle(pool)
            new_held, det.dev_deck = pool[:n_held], pool[n_held:]
            opp_ps.dev_cards = {c: 0 for c in DevCard}
            opp_ps.dev_bought_this_turn = {c: 0 for c in DevCard}
            for i, c in enumerate(new_held):
                opp_ps.dev_cards[c] += 1
                if i < n_bought_now:  # same-turn purchases stay unplayable
                    opp_ps.dev_bought_this_turn[c] += 1

        # --- independent chance streams, same latent structure ---
        det.rng = random.Random(rng.randrange(2**63))
        det.dice.reseed(rng.randrange(2**63))
        return det


def _prior_hand(state: GameState, player: int, rng: random.Random):
    """Production-weighted hand of the correct public size (no tracker)."""
    from engine import Resource

    hand = {r: 0 for r in Resource}
    weights = _production_weights(state, player)
    for _ in range(state.players[player].hand_size()):
        hand[_weighted_pick(weights, rng)] += 1
    return hand
