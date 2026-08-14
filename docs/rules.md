# rules.md

# 1v1 Colonist Rules Specification for Engine and Trainer

## 1. Scope

This document defines the target ruleset for a **1v1 Catan engine / tactics trainer** modeled on **Colonist ranked 1v1**, with explicit product constraints:

- 2 players only
- **15 victory points** to win
- **Balanced Dice** enabled
- **Fair Robber / Friendly Robber** enabled
- **No player-to-player trading**
- Only **4:1 bank trades** and **port trades** are allowed
- Random board generation with explicit placement constraints
- Base-game development cards only

This is an **implementation spec**, not a legal rules document. Where Colonist's public documentation is incomplete, this spec chooses a concrete product behavior and marks it clearly.

---

## 2. Core win condition and match settings

### 2.1 Victory condition
- First player to reach **15 VP** wins.
- A win is checked immediately after an action that changes visible or hidden VP and legally ends the game.

### 2.2 Discard limit
- On a 7, players with **more than 9 cards** discard half their hand, rounded down.

### 2.3 Turn timer
- Colonist ranked 1v1 uses a short timer, but for the engine and trainer this is **not part of game logic**.
- The live bot may use time budgets, but offline evaluation and puzzle generation are unconstrained.

---

## 3. Board definition

### 3.1 Board topology
Use the standard base Catan board graph:
- 19 hexes
- 54 intersections (vertices)
- 72 edges
- 9 ports on the coast

### 3.2 Terrain distribution
Use the standard base-game terrain multiset:
- 4 wood
- 4 sheep
- 4 wheat
- 3 brick
- 3 ore
- 1 desert

### 3.3 Number-token multiset
Use the standard base-game number-token multiset:
- 2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12
- Desert receives no number token.

### 3.4 Port distribution
Use the standard base-game port multiset:
- 4 generic 3:1 ports
- 1 wood 2:1 port
- 1 sheep 2:1 port
- 1 wheat 2:1 port
- 1 brick 2:1 port
- 1 ore 2:1 port

---

## 4. Random board generation

## 4.1 Important note on Colonist fidelity
Publicly available Colonist material clearly supports the standard **6/8 non-adjacency** rule. Public evidence for additional hard constraints like **2/12 non-adjacency** is weaker and not fully specified in official rules text.

Because your product goal explicitly wants those constraints, this spec treats them as **required product constraints** unless later empirical reverse-engineering of Colonist boards shows otherwise.

So this engine should support two modes:

1. **strict_product_mode**
   - Enforce 6/8 non-adjacent
   - Enforce 2/12 non-adjacent
   - Optionally enforce additional anti-clumping constraints

2. **colonist_calibrated_mode**
   - Start with 6/8 non-adjacent
   - Keep extra constraints configurable
   - Tune against observed Colonist boards later

For now, use **strict_product_mode**.

## 4.2 Required generation constraints
When generating a board, enforce:

### Terrain constraints
- Exactly one desert
- Standard terrain counts listed above

### Number-token constraints
- No 6 adjacent to any 6 or 8
- No 8 adjacent to any 6 or 8
- No 2 adjacent to any 2 or 12
- No 12 adjacent to any 2 or 12

### Optional recommended anti-clumping constraints
These are not assumed official Colonist rules, but are useful for better puzzle diversity and board quality:
- No three identical resources forming a triangle around a single intersection
- No three high-probability numbers (5/6/8/9) concentrated around one coastal lane beyond a threshold
- Avoid pairing both copies of the same high number on the same resource type if possible

## 4.3 Board generation algorithm

### Step 1: shuffle terrain
- Randomly assign the 19 terrain hexes subject only to terrain counts.

### Step 2: place desert
- The desert is one of the terrain hexes and starts with the robber on it.

### Step 3: assign number tokens
- Randomly assign the 18 number tokens to non-desert hexes.
- If constraints are violated, reject and resample.

### Step 4: assign ports
- Randomly assign the 9 ports around the coast using the standard port multiset.
- Preserve valid port topology so each port touches exactly 2 coastal intersections.

### Step 5: validate
The board is valid iff:
- terrain counts are correct
- number counts are correct
- 6/8 adjacency constraint passes
- 2/12 adjacency constraint passes
- port counts are correct

### Step 6: persist canonical representation
Store the board as:
- hex list with terrain + number + robber bit
- port list with type + coastal edge index
- graph adjacency lists for hexes, vertices, edges

---

## 5. Setup phase

## 5.1 Initial placements
Use the normal snake draft placement:
- Player A places settlement + road
- Player B places settlement + road
- Player B places second settlement + road
- Player A places second settlement + road

## 5.2 Distance rule
A settlement may be placed only if every adjacent intersection is empty.

## 5.3 Road placement during setup
The road attached to a newly placed setup settlement must be on an incident edge of that settlement.

## 5.4 Starting resources
Each player receives starting resources from the hexes adjacent to their **second** settlement only, excluding desert and blocked production from no-number desert.

---

## 6. Turn structure

Each normal turn proceeds as follows.

### 6.1 Start-of-turn state checks
Resolve any pending forced effects from the previous action sequence if your engine uses a staged action model.

### 6.2 Dice / production step
- The active player rolls using the **Balanced Dice** process.
- If the result is not 7, all unblocked settlements and cities adjacent to matching hexes produce resources.
- Settlement = 1 resource from each matching adjacent hex.
- City = 2 resources from each matching adjacent hex.

### 6.3 If the roll is 7
Apply in order:
1. Players with hand size > 9 discard half, rounded down.
2. Active player chooses a legal robber destination hex.
3. If at least one opponent building is adjacent and the Fair Robber rule allows theft, active player chooses a victim.
4. One random resource is stolen from the victim if the victim has any resources.

### 6.4 Action phase
The active player may perform any number of legal actions in any legal order until they pass/end turn.

Allowed actions:
- build road
- build settlement
- build city
- buy development card
- play one development card, subject to timing rules
- maritime trade with bank / ports only
- end turn

Not allowed:
- player-to-player trade
- any negotiation action

---

## 7. Costs and build rules

### 7.1 Road
Cost:
- 1 wood
- 1 brick

Placement rule:
- Must connect to the player's road network or a newly placed setup settlement
- Cannot pass through an opponent settlement/city

### 7.2 Settlement
Cost:
- 1 wood
- 1 brick
- 1 sheep
- 1 wheat

Placement rule:
- Must connect to one of the player's roads
- Must satisfy the distance rule
- Cannot be placed on an occupied intersection

### 7.3 City
Cost:
- 3 ore
- 2 wheat

Placement rule:
- Must upgrade the player's existing settlement

### 7.4 Development card purchase
Cost:
- 1 ore
- 1 wheat
- 1 sheep

Rule:
- Draw top card from development deck
- A dev card bought this turn cannot be played this turn, except hidden VP scoring is immediate if your implementation tracks hidden VP in hand

---

## 8. Development cards

Use the standard base-game deck:
- 14 Knight
- 5 Victory Point
- 2 Road Building
- 2 Year of Plenty
- 2 Monopoly

## 8.1 Knight
- Move robber to a legal hex
- Optionally steal from one adjacent opponent if eligible
- Counts toward Largest Army

## 8.2 Road Building
- Place up to 2 free roads legally
- If only 1 legal road can be placed, place 1
- If 0 legal roads, the card has no effect beyond being consumed only if your rules permit legal play; safer implementation is to require at least 1 legal road before play

## 8.3 Year of Plenty
- Gain any 2 resources from the bank, subject to bank availability if you model a finite hidden bank

## 8.4 Monopoly
- Declare one resource type
- Take all cards of that type from the opponent in 1v1

## 8.5 Victory Point
- Hidden in hand
- Counts toward total VP immediately for win checking, but does not count as visible points for Friendly Robber targeting

## 8.6 Play limit
- At most **one non-VP development card** may be played per turn

---

## 9. Robber rules

## 9.1 Robber placement
The robber blocks production on its current hex.

## 9.2 Fair Robber / Friendly Robber
For this product, use the following rule:
- A player may not be robbed from or blocked by the robber until they have **more than 2 visible victory points**.

Visible VP includes:
- settlements
- cities
- longest road
- largest army

Visible VP excludes:
- hidden VP development cards

If the only legal robber hexes would target an ineligible player, then robber movement must still occur, but the destination must obey the no-targeting rule as implemented by the simulator. A practical implementation is:
- legal robber destination set excludes hexes adjacent to any opponent who is not yet robber-eligible
- if no such hex exists, allow any non-current robber hex but do not steal

## 9.3 Robber cannot remain on same hex
Use the standard rule:
- when moved, robber must go to a different hex

---

## 10. Trading rules

## 10.1 No domestic trade
The engine forbids all player-to-player trades.

## 10.2 Maritime / bank trade only
The active player may trade with:
- bank at 4:1
- generic port at 3:1 if owned
- matching resource port at 2:1 if owned for that resource

## 10.3 Port ownership
A player has access to a port if they own a settlement or city on one of the port's two coastal intersections.

## 10.4 Bank availability
Because Colonist hides bank cards in ranked play, choose one of two modes:

### recommended_engine_mode_v1
- treat bank as effectively unlimited for ordinary resource exchange validation
- this simplifies search and labeling

### parity_mode_later
- model finite bank counts exactly
- hide counts from the players
- include bank counts in latent simulator state

Use **recommended_engine_mode_v1** first, but keep the bank API abstract.

---

## 11. Longest Road

## 11.1 Award rule
- Longest Road is worth 2 VP.
- A player must have a road length of at least 5 to claim it.
- If both players tie at the same max length, the current holder retains it; if no current holder and tied, nobody gets it.

## 11.2 Path definition
- Use the standard simple-path interpretation on the player's road graph.
- Opponent settlements/cities break continuity through that intersection.
- The owner's own settlements/cities do not break continuity.

---

## 12. Largest Army

## 12.1 Award rule
- Largest Army is worth 2 VP.
- A player must have played at least 3 Knights to claim it.
- On a tie, current holder retains it; if no current holder and tied, nobody gets it.

---

## 13. Balanced Dice model

## 13.1 Product requirement
The simulator must not use plain i.i.d. 2d6 in ranked-parity mode.

## 13.2 Minimal balanced dice interface
Implement the dice generator behind an interface like:

```python
class DicePolicy:
    def next_roll(self, public_state, latent_state, active_player) -> int:
        ...
```

## 13.3 Recommended staged implementation

### version 1: IID baseline
- Standard 2d6 distribution
- Used only for debugging and ablations

### version 2: deck-based balanced dice
- Use a 36-card deck corresponding to all ordered or unordered 2d6 outcomes, depending on chosen implementation
- Reshuffle when deck count reaches threshold
- Add anti-repeat weighting

### version 3: 1v1 fair-seven adjustment
- Modify the probability of rolling 7 based on recent seven history and player-specific seven allocation state

Because Colonist's exact internal implementation details may change, keep this modular and separately testable.

---

## 14. Hidden information model

The true game state includes hidden components:
- opponent resource hand
- opponent dev cards in hand
- dev deck order
- optionally bank inventory
- balanced-dice latent memory state

The engine must support both:

### perfect_state_for_simulation
Used internally for rollouts and data generation.

### observation_state_for_player
Used by the policy/value system and trainer UI.

The trainer may optionally reveal hidden information in some puzzle categories, but default puzzles should use only public information plus permitted inference.

---

## 15. Differences from standard 3-4 player Catan

Compared with standard base Catan, this spec differs in these important ways:

- 2 players instead of 3-4
- 15 VP instead of 10 VP
- discard threshold 9 instead of 7
- Friendly Robber / Fair Robber is on
- Balanced Dice is on
- no player-to-player trading
- only bank/port trades allowed
- board generator uses explicit product constraints for both 6/8 and 2/12 adjacency

These differences materially change strategy, search depth, and neural-network labels.

---

## 16. Required engine test cases

At minimum, add unit tests for:
- setup legality and snake order
- 6/8 and 2/12 board-generation validation
- production from settlements and cities
- robber discard on >9 cards only
- Friendly Robber visibility logic
- longest road edge cases with blocking settlements
- largest army transfer
- dev-card timing rules
- port ownership and 2:1 / 3:1 / 4:1 trade legality
- no domestic trade enforcement
- hidden VP excluded from visible VP robber eligibility
- balanced-dice API determinism under fixed seed
