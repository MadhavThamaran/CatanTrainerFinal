# balanced_cie_rules.md

## Purpose

This document describes the balanced dice rules used by the current game logic so they can be mirrored in the bot environment and training setup.

This spec is based on the existing `DiceControllerBalanced` implementation in the codebase.

---

## Overview

The balanced dice system does **not** roll two fair dice independently every turn.

Instead, it uses a **weighted deck of all 36 possible two-dice outcomes** and dynamically adjusts the probability of each total before drawing. This keeps long-run frequencies close to standard Catan odds while also reducing streakiness and reducing unfair concentrations of 7s on the same player.

The system has three main components:

1. A **36-card standard dice deck**
2. A **recent-roll anti-streak adjustment**
3. A **7-balancing adjustment across players**

---

## 1. Standard Dice Deck

The balanced dice controller starts from the full multiset of all 36 ordered outcomes of two six-sided dice.

That means the totals appear with standard frequencies:

- 2: 1 combination
- 3: 2 combinations
- 4: 3 combinations
- 5: 4 combinations
- 6: 5 combinations
- 7: 6 combinations
- 8: 5 combinations
- 9: 4 combinations
- 10: 3 combinations
- 11: 2 combinations
- 12: 1 combination

Examples:
- Total 7 contains:
  - (1,6), (2,5), (3,4), (4,3), (5,2), (6,1)
- Total 6 contains:
  - (1,5), (2,4), (3,3), (4,2), (5,1)

Each total is stored as a bucket of dice pairs.

---

## 2. Reshuffling Rule

The deck is reshuffled back to the full 36 ordered outcomes when the number of cards left in the deck falls below:

- `minimumCardsBeforeReshuffling = 13`

So:
- start with 36 cards
- remove one drawn dice pair each roll
- when fewer than 13 remain, rebuild the full 36-card deck

This preserves long-run standard dice frequencies while still allowing local weighting adjustments.

---

## 3. Base Probability Before Adjustments

Before any special balancing logic is applied, each total gets probability weight equal to:

```text
weight(total) = remaining_pairs_for_total / cards_left_in_deck
```

So the current deck composition determines the base probabilities.

---

## 4. Recent-Roll Anti-Streak Adjustment

The controller tracks the most recent totals rolled:

- `maximumRecentRollMemory = 5`

For each total, it also tracks how many times that total appears in the recent memory:

- `recentlyRolledCount`

Before drawing a new result, each total's probability is reduced according to how recently and how often it has appeared:

```text
probabilityReduction = recentlyRolledCount * 0.34
probabilityMultiplier = 1 - probabilityReduction
adjustedWeight = baseWeight * probabilityMultiplier
```

Where:

- `probabilityReductionForRecentlyRolled = 0.34`

Implications:
- rolled 0 times recently -> multiplier = 1.00
- rolled 1 time recently -> multiplier = 0.66
- rolled 2 times recently -> multiplier = 0.32
- rolled 3 times recently -> multiplier = -0.02 -> clamped to 0
- rolled 4+ times recently -> clamped to 0

If a weight becomes negative, it is clamped to 0.

### Summary

This system strongly suppresses recently repeated totals and reduces streaky behavior.

---

## 5. Drawing a Result

After all probability adjustments are applied:

1. Compute total probability mass across totals 2 through 12
2. Sample a random number from `[0, totalProbabilityWeight]`
3. Select the total bucket based on cumulative weight
4. Randomly choose one dice pair from that bucket
5. Remove that pair from the deck
6. Record the total in recent roll memory
7. Decrement `cardsLeftInDeck`

So the system:
- samples **totals** using weighted probabilities
- then samples a specific **ordered dice pair** uniformly from the chosen total bucket

---

## 6. 7-Balancing Across Players

The system adds special handling for rolls of 7 in multiplayer games.

This logic only applies when:

- `numberOfPlayers >= 2`

The goal is to reduce unfair concentration of 7s on the same player and reduce extreme 7 streaks.

It does this using two adjustments:

1. **7 streak adjustment**
2. **7 imbalance adjustment by player**

These are combined and applied only to the probability weight of total 7.

---

## 7. 7 Streak Adjustment

The controller tracks:

- which player most recently experienced a streak of 7s
- how long that streak is

Configuration:

- `probabilityReductionForSevenStreaks = 0.4`

The streak adjustment constant is:

```text
streakAdjustment = 0.4 * streakCount * sign
```

Where:
- `sign = -1` if the current player is the player currently on the bad side of the 7 streak
- `sign = +1` otherwise

Interpretation:
- if a player has been hit by consecutive 7s, their future chance of seeing another 7 is reduced
- the other player's chance of seeing a 7 is increased

---

## 8. 7 Imbalance Adjustment by Player

The controller also tracks the total number of 7s rolled against each player.

For a given player:

```text
percentageOfTotalSevens = sevensRolledAgainstPlayer / totalSevensRolled
idealPercentage = 1 / numberOfPlayers
imbalanceAdjustment = 1 + ((idealPercentage - percentageOfTotalSevens) / idealPercentage)
```

Interpretation:
- if a player has received **fewer** than their fair share of 7s, their 7 probability is increased
- if a player has received **more** than their fair share of 7s, their 7 probability is reduced

If there have not yet been enough total 7s to compare fairly, the adjustment defaults to 1.

---

## 9. Combined 7 Adjustment

The total 7 adjustment is:

```text
sevenProbabilityAdjustment = playerSevensAdjustment + streakAdjustment
```

Then it is clamped to:

```text
min = 0
max = 2
```

Finally, only the weight for total 7 is multiplied by this value.

So:
- 7 can be fully suppressed down to 0x
- or boosted up to 2x
- but only after accounting for recent streaks and cross-player fairness

---

## 10. Full Roll Procedure

For a given player's dice roll:

1. Initialize that player's 7-tracking entry if needed
2. If fewer than 13 cards remain, reshuffle the full 36-card deck
3. Recompute base probabilities from deck composition
4. Reduce weights for recently rolled totals
5. Adjust the probability of total 7 based on:
   - recent 7 streaks
   - player-specific 7 imbalance
6. Sample a total using the adjusted weights
7. Randomly choose a dice pair from that total bucket
8. Remove that pair from the deck
9. Update recent-roll memory
10. If the roll was a 7:
    - update total sevens against that player
    - update current 7 streak tracking

---

## 11. Key Constants

These are the core constants used by the current implementation:

```text
minimumCardsBeforeReshuffling = 13
probabilityReductionForRecentlyRolled = 0.34
probabilityReductionForSevenStreaks = 0.4
maximumRecentRollMemory = 5
```

---

## 12. Design Intent

This balanced dice system is designed to preserve the feel of normal Catan dice while reducing frustrating variance.

Specifically, it aims to:

- preserve standard long-run dice frequencies
- reduce short-term streaks of repeated totals
- reduce repeated punishment from 7s on the same player
- keep dice outcomes closer to fair over time

This is **not** the same as rolling independent fair dice every turn.

It is a hybrid between:
- a deck-based dice system
- recent-roll suppression
- player-specific balancing for 7s

---

## 13. Implications for the Bot / RL Environment

If the PPO environment is meant to match the actual game rules, it should not use plain IID dice rolls.

Instead, the environment should implement the same balanced dice logic, including:

- 36-card ordered dice deck
- reshuffling when fewer than 13 cards remain
- recent-roll memory of length 5
- 0.34 suppression per recent appearance
- player-specific 7 balancing
- 7 streak suppression / compensation
- clamping 7 adjustment between 0 and 2

If exact parity with the game client is the goal, this file should be treated as the behavioral spec.

---

## 14. Recommended Engineering Notes

When implementing this logic elsewhere:

1. Keep the deck state persistent across turns
2. Track recent totals, not just recent dice pairs
3. Track 7 counts separately per player
4. Track current consecutive 7 streak target player
5. Apply recent-roll adjustments before sampling
6. Apply 7-specific player balancing after recent-roll adjustments
7. Clamp negative weights to 0
8. Clamp the final 7 multiplier to `[0, 2]`

---

## 15. Short Summary

Balanced dice in this codebase means:

- start from a 36-outcome deck
- draw without replacement until reshuffle
- suppress recently repeated totals
- rebalance 7s across players
- reduce 7 streaks on the same player

This should be the reference behavior for any simulator or training environment that wants to match the actual game logic.
