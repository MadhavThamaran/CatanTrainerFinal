"""Dice policies (docs/balanced_die_rules.md): seeded determinism, deck
draw-without-replacement, recent-roll suppression, sane long-run frequencies."""
from collections import Counter

from engine import BalancedDice, IIDDice, ScriptedDice

_STANDARD_FREQ = {
    2: 1 / 36, 3: 2 / 36, 4: 3 / 36, 5: 4 / 36, 6: 5 / 36, 7: 6 / 36,
    8: 5 / 36, 9: 4 / 36, 10: 3 / 36, 11: 2 / 36, 12: 1 / 36,
}
_BUCKET_SIZES = {t: round(f * 36) for t, f in _STANDARD_FREQ.items()}


def _roll_many(dice, n, players=(0, 1)):
    return [dice.next_roll(players[i % len(players)]) for i in range(n)]


def test_balanced_dice_deterministic_under_fixed_seed():
    a = _roll_many(BalancedDice(seed=123), 500)
    b = _roll_many(BalancedDice(seed=123), 500)
    assert a == b
    assert a != _roll_many(BalancedDice(seed=124), 500)


def test_clone_replays_identically():
    dice = BalancedDice(seed=9)
    _roll_many(dice, 50)
    twin = dice.clone()
    assert _roll_many(dice, 100) == _roll_many(twin, 100)


def test_iid_dice_deterministic_and_in_range():
    a = _roll_many(IIDDice(seed=5), 200)
    assert a == _roll_many(IIDDice(seed=5), 200)
    assert all(2 <= t <= 12 for t in a)


def test_scripted_dice_returns_script():
    dice = ScriptedDice([7, 3, 11])
    assert [dice.next_roll(0) for _ in range(4)] == [7, 3, 11, 11]


def test_deck_draws_without_replacement_before_reshuffle():
    # The deck reshuffles when < 13 cards remain, i.e. after 24 draws.
    # Within those 24 draws no total can exceed its bucket size.
    for seed in range(20):
        rolls = _roll_many(BalancedDice(seed=seed), 24)
        for total, count in Counter(rolls).items():
            assert count <= _BUCKET_SIZES[total], f"seed {seed}: {total} x{count}"


def test_recent_roll_suppression_caps_repeats_in_window():
    # 3 appearances in the 5-roll memory drive a total's weight to zero
    # (1 - 3 * 0.34 < 0), so no total can appear 4 times in any 5-roll window.
    for seed in range(10):
        rolls = _roll_many(BalancedDice(seed=seed), 2000)
        for i in range(len(rolls) - 4):
            window = rolls[i : i + 5]
            assert max(Counter(window).values()) <= 3, f"seed {seed} @ {i}"


def test_long_run_frequencies_stay_near_standard():
    rolls = _roll_many(BalancedDice(seed=7), 30_000)
    freq = Counter(rolls)
    for total, expected in _STANDARD_FREQ.items():
        observed = freq[total] / len(rolls)
        assert abs(observed - expected) < 0.03, f"{total}: {observed:.3f} vs {expected:.3f}"


def test_sevens_are_shared_between_players():
    dice = BalancedDice(seed=11)
    sevens = Counter()
    for i in range(4000):
        player = i % 2
        if dice.next_roll(player) == 7:
            sevens[player] += 1
    assert sevens[0] > 0 and sevens[1] > 0
    total = sevens[0] + sevens[1]
    # The 7-balancer should keep the split reasonably fair.
    assert abs(sevens[0] - sevens[1]) / total < 0.25
