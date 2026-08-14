# 1v1 Colonist-Inspired Engine Spec

## Tag Legend

| Tag | Meaning |
|---|---|
| **[Verified Colonist]** | Supported by Colonist docs/blogs |
| **[Verified CATAN]** | Standard official CATAN rules |
| **[Product Constraint]** | Explicit design decision for this engine |
| **[Engine Assumption]** | Implementation choice for simplicity |
| **[To Calibrate]** | Needs empirical validation vs Colonist |

---

## 1. Scope

A **1v1 Catan engine and tactics trainer** inspired by Colonist ranked 1v1.

| Setting | Tag |
|---|---|
| 2 players only | [Verified Colonist] |
| 15 victory points to win | [Verified Colonist] |
| Balanced Dice enabled | [Verified Colonist] |
| Friendly Robber enabled | [Verified Colonist] |
| No player-to-player trading | [Product Constraint] |
| Bank (4:1) and port trades only | [Product Constraint] |
| Random board generation with explicit constraints | [Product Constraint] |
| Base-game development cards only | [Verified CATAN] |

---

## 2. Core Win Condition

### 2.1 Victory

- First player to reach **15 VP on their own turn wins** — [Verified Colonist] + [Verified CATAN]
- Hidden VP cards count toward win immediately — [Verified CATAN]

### 2.2 Discard Rule

- On a 7, players with **> 9 cards discard half (floor)** — [Verified Colonist]

### 2.3 Timer

- Timer is not part of engine logic — [Engine Assumption]

---

## 3. Board Definition

### 3.1 Topology — [Verified CATAN]

- 19 hexes, 54 vertices, 72 edges, 9 ports

### 3.2 Terrain Distribution — [Verified CATAN]

- 4 wood, 4 sheep, 4 wheat
- 3 brick, 3 ore
- 1 desert

### 3.3 Number Tokens — [Verified CATAN]

```
2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12
```

### 3.4 Ports — [Verified CATAN]

- 4 × 3:1
- 5 × 2:1 (one per resource)

---

## 4. Board Generation

### 4.1 Modes

**`strict_product_mode` (default)** — [Product Constraint]
- Enforce 6/8 non-adjacent
- Enforce 2/12 non-adjacent
- Optional anti-clumping

**`colonist_calibrated_mode`** — [To Calibrate]
- Enforce 6/8 non-adjacent
- Tune others later

### 4.2 Constraints

**Required**
- No 6 adjacent to 6/8 — [Verified CATAN]
- No 8 adjacent to 6/8 — [Verified CATAN]
- No 2 adjacent to 2/12 — [Product Constraint]
- No 12 adjacent to 2/12 — [Product Constraint]

**Optional (quality constraints)** — [Product Constraint]
- No triple-resource triangle
- Avoid extreme clustering of 5/6/8/9
- Avoid duplicate high numbers on same resource

### 4.3 Generation Algorithm — [Engine Assumption]

1. Shuffle terrain
2. Place desert
3. Assign numbers (reject if invalid)
4. Assign ports
5. Validate

---

## 5. Setup Phase

| Rule | Tag |
|---|---|
| Placement order: A → B → B → A (snake draft) | [Verified CATAN] |
| No adjacent settlements (distance rule) | [Verified CATAN] |
| Setup road must be incident to settlement | [Verified CATAN] |
| Starting resources from second settlement only | [Verified CATAN] |

---

## 6. Turn Structure

### 6.1 Dice

- Use Balanced Dice — [Verified Colonist]

### 6.2 Production

- Settlement = 1 resource per adjacent hex — [Verified CATAN]
- City = 2 resources per adjacent hex — [Verified CATAN]

### 6.3 Roll = 7

Order of operations — [Verified CATAN] + [Verified Colonist]:

1. Discard (players with > 9 cards)
2. Move robber
3. Attempt steal

### 6.4 Action Phase

Allowed actions — [Verified CATAN] + [Product Constraint for trade]:

- Build (road / settlement / city)
- Buy dev card
- Play 1 dev card
- Trade with bank or ports
- End turn

---

## 7. Build Rules — [Verified CATAN]

### Road
- Must connect to player's network
- Blocked by opponent settlement

### Settlement
- Must connect to a road
- Must satisfy distance rule

### City
- Upgrade of existing settlement only

---

## 8. Development Cards

**Deck composition** — [Verified CATAN]

| Card | Count |
|---|---|
| Knight | 14 |
| Victory Point | 5 |
| Road Building | 2 |
| Year of Plenty | 2 |
| Monopoly | 2 |

**Rules**
- Max 1 non-VP dev card played per turn — [Verified CATAN]
- Cannot play a dev card the same turn it was bought — [Verified CATAN]

**Edge cases**
- Road Building requires ≥ 1 legal road placement to play — [Engine Assumption]

---

## 9. Robber (Friendly Robber)

### Rule — [Verified Colonist]

Cannot rob or block players with **≤ 2 visible VP**.

### Visible VP includes — [Verified CATAN]

- Settlements
- Cities
- Longest road bonus
- Largest army bonus

### Visible VP excludes — [Verified Colonist]

- VP development cards

### Fallback Behavior — [Engine Assumption]

If no valid opponent targets exist:

- Move robber anyway
- Prefer non-impact or self-adjacent placement
- No steal occurs

*(Approximation of Colonist behavior)*

---

## 10. Trading

- No player-to-player trading — [Product Constraint]
- Allowed: 4:1 bank, 3:1 port, 2:1 port — [Verified CATAN] + [Product Constraint]
- Bank treated as infinite supply — [Engine Assumption]

---

## 11. Longest Road — [Verified CATAN]

- Requires path length ≥ 5
- Worth 2 VP
- Opponent settlement breaks path
- Tie → current holder retains

---

## 12. Largest Army — [Verified CATAN]

- Requires ≥ 3 knights played
- Worth 2 VP
- Tie → current holder retains

---

## 13. Balanced Dice

### Colonist Behavior — [Verified Colonist]

- 36-outcome dice deck
- Reshuffle near depletion
- Anti-repeat bias (~30% reduction on consecutive same sum)

### 1v1 Adjustment — [Verified Colonist]

- Seven probability dynamically adjusted per player to reduce streaks

### Implementation — [Engine Assumption]

```python
class DicePolicy:
    def next_roll(self, public_state, latent_state, active_player) -> int:
        ...
```

---

## 14. Hidden Information — [Engine Assumption]

**Hidden from each player:**

- Opponent hand composition
- Opponent dev cards held
- Dev deck order
- Dice controller internal state
- Bank state (optional)

**Two state views:**

- `perfect_state` — omniscient, used for simulation and search
- `observation_state` — player-visible only, used as policy input

---

## 15. Key Differences vs Standard Catan

| Difference | Tag |
|---|---|
| 2 players | [Verified Colonist] |
| 15 VP to win | [Verified Colonist] |
| Discard threshold at > 9 cards | [Verified Colonist] |
| Friendly Robber | [Verified Colonist] |
| Balanced Dice | [Verified Colonist] |
| No domestic trade | [Product Constraint] |

---

## 16. Required Tests — [Engine Requirement]

- [ ] Setup legality
- [ ] Board constraint validation
- [ ] Production correctness
- [ ] Discard logic (threshold, floor)
- [ ] Robber rules (Friendly Robber visible VP calculation)
- [ ] Longest road edge cases (breaks, ties)
- [ ] Dev card timing (same-turn buy restriction)
- [ ] Port access
- [ ] Trade restrictions
- [ ] Hidden VP exclusion from visible VP count
- [ ] Dice determinism (replay from seed)

---

## Next Steps

- [ ] Convert to Python enums + config flags
- [ ] Design state/action representations for RL and search