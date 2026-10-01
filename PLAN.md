# PLAN.md — 1v1 Catan Tactics Trainer

*Authoritative ruleset: `docs/rules.md`, `docs/1v1_Colonist_Inspired_Spec.md`, `docs/balanced_die_rules.md`. This plan references those specs rather than restating them.*

---

## 1. Executive summary

We are building a chess.com-style tactics trainer for 1v1 Colonist-variant Catan (15 VP, Balanced Dice, Friendly Robber, no domestic trade): the user is shown a position, proposes a move, and earns graded points based on how close their move is to the engine's best move. I agree with the framing baked into the brief — **this project is "build Stockfish first, then puzzles"**: every downstream feature (puzzle generation, ranked scoring, explanations, difficulty rating) consumes one primitive, *a per-move quality score for every legal move in a position*. The recommended evaluator is **determinized MCTS with a self-play-trained policy/value network (AlphaZero-style), staged behind a heuristic expected-value baseline** — search plus a bootstrapped value function sidesteps the long-horizon credit-assignment failure encountered with PPO, produces per-move ranked Q-values natively, and the no-trade 1v1 variant is unusually friendly to determinization because almost all hidden information is trackable. The build order is: rules engine with a full test suite → constrained board generator → heuristic baseline agents → determinized MCTS engine → self-play training loop → puzzle mining + scoring → trainer UI. The engine core (Stage 1) is scaffolded in this repo now (see `README.md`); everything else builds on it.

---

## 2. ML / search recommendation (the hard part)

### 2.1 Recommendation

**Primary: AlphaZero-style MCTS with a learned policy/value network, adapted to imperfect information by root-level determinization (sample opponent hidden state consistent with the observation, search each sample, aggregate root statistics).** Reached in three stages so there is always a working evaluator:

- **Stage A — Heuristic expected-value evaluator (weeks, no ML).** Closed-form production expectation, resource diversity, port synergy, expansion potential, robber exposure. Already strong enough to *rank placement-phase moves credibly* and to serve as the rollout policy / leaf evaluator for Stage B. Placement puzzles can ship on this.
- **Stage B — Determinized MCTS with heuristic value (the first real engine).** UCT search over the exact rules engine; hidden state (opponent hand remainder, dev deck order, dice-controller latent state) is sampled per determinization; leaf evaluation by the Stage A heuristic plus truncated rollouts. This already produces per-move Q-values good enough to label first-generation puzzles end to end.
- **Stage C — Self-play-trained policy/value network (AlphaZero loop).** Replace heuristic priors and leaf values with a network trained on search-improved targets from self-play. Strength scales with compute; the puzzle pipeline is unchanged because the interface (per-move root Q-values + visit counts) is identical.

### 2.2 Why this fixes the PPO credit-assignment problem

The observed failure — *"PPO can't tell that the road built 100 actions ago is why this position is winning"* — is structural, not a tuning issue. PPO propagates a sparse terminal reward backward through the return/GAE; over hundreds of actions the advantage signal for any single early action drowns in variance, and the dice add exogenous noise that PPO cannot distinguish from decision quality. The recommended approach attacks this from two independent directions:

1. **Search does the credit assignment forward, not backward.** A move's score is not inferred by correlating it with a distant win — it is *computed*, by explicitly simulating the futures that follow it under strong play and averaging their values. The road's value shows up in the search tree as better reachable futures *right now*, no 100-step reward propagation required.
2. **The value network is trained by bootstrapping, not Monte Carlo returns.** Targets are search-improved values and (eventually) outcomes of *strong* self-play, so each training signal spans a short effective horizon. This is exactly the mechanism by which AlphaZero learns games with far longer action sequences and sparser rewards than Catan.

Additionally, PPO's output is a policy (a preferred move), not a *ranking with scores* — you would need a separate Q-critic to score all legal moves, and an actor-critic's critic is precisely the component that suffers most from the long-horizon variance. MCTS root statistics give the ranking for free, which is the exact artifact puzzle scoring consumes.

### 2.3 Imperfect information: why determinization is the right level of machinery

This variant is unusually determinization-friendly, and this is worth stating precisely because it is the reason we can skip heavyweight solvers:

- **No player-to-player trading** means every resource the opponent gains or spends is publicly inferable: production is public (dice + board), every build/buy has a known cost, bank/port trades are public. The *only* hidden perturbations to the opponent's hand are robber steals (one unknown card each) and Monopoly reveals information rather than hiding it. A simple card-tracking module maintains an exact distribution (often an exact count) over the opponent's hand.
- Remaining hidden state — opponent dev cards in hand, dev deck order, balanced-dice latent memory — is low-entropy and easy to sample from exactly (we own the simulator).
- Determinization's known weaknesses (strategy fusion, non-locality) bite hardest in games built on information asymmetry — bluffing, hidden roles, signaling. Catan 1v1 without trading has almost no bluffing dimension; the dominant skill is production/position evaluation and race calculation. The error introduced by determinization is second-order here.

Mechanism: at the root, sample K determinizations (opponent hand consistent with tracked constraints, shuffled dev deck remainder, dice-controller state — which is *exactly known* to the simulator and can be carried as-is in `perfect_state` rollouts); run MCTS on each (PIMC) or share a tree across them (ISMCTS-style, single tree with per-node determinization filtering); aggregate per-move value across samples. Start with PIMC (simplest, trivially parallel); move to ISMCTS only if strategy-fusion artifacts show up in labeled puzzles.

**Balanced dice in search:** rollouts use the real `BalancedDice` controller (it is part of latent state and materially shifts 7-timing); chance nodes in the tree can use its adjusted distribution directly since the simulator exposes it. This is a fidelity advantage of owning the ruleset.

### 2.4 Alternatives considered

| Method | Verdict | Reason |
|---|---|---|
| **AlphaZero-style MCTS + policy/value net (determinized)** | ✅ **Recommended** | Per-move Q-values native; bootstrapped value kills the credit-assignment problem; proven recipe; staged path means value ships before any NN exists. |
| **PIMC / ISMCTS (no learned net)** | ✅ As Stage B / fallback | Same search skeleton; weaker without learned priors but fully functional. This *is* the de-risked core of the recommendation. |
| **ReBeL / Player of Games / CFR-family** | ❌ Overkill | Theoretically superior handling of hidden information, but the hidden-information content of this variant is small (see 2.3) and these methods cost 5–10× the engineering (public belief states, subgame solving) for gains concentrated in games we are not playing (poker-like). Revisit only if determinization demonstrably mislabels puzzles. |
| **PPO / model-free deep RL as the engine** | ❌ As primary | Credit-assignment failure already observed (§2.2); no native move ranking. **Retained role:** an optional cheap policy trained by imitation/short-horizon RL can serve as a *rollout policy or prior* inside MCTS — useful, never load-bearing. |
| **Heuristic EV only** | ❌ As endpoint, ✅ as Stage A | No lookahead → misses tactical lines (robber timing, LR races, dev-card tempo); but ideal bootstrap, leaf evaluator, and sanity baseline. |

### 2.5 Risks and fallback

- **Risk:** self-play training (Stage C) underdelivers or is compute-starved. **Fallback:** Stage B (determinized MCTS + tuned heuristics + more search time) is retained as a complete engine. Puzzle labeling is offline — we can spend minutes per position; the engine only needs to be *clearly stronger than the target user*, not superhuman.
- **Risk:** determinization mislabels positions where information-gathering itself is the point (rare here). **Mitigation:** flag puzzles where per-determinization best moves disagree wildly; exclude or hand-review.
- **Risk:** evaluation noise makes "best move" unstable between runs. **Mitigation:** puzzle admission criteria require a minimum Q-gap and cross-seed stability (§5 of the stage plan).

---

## 3. Milestone roadmap

Critical path: **M1 → M2 → M3 → M4 → M6 → M7** (M5 parallelizes with M4; M8 parallelizes with M6+).

| # | Milestone | Depends on | Exit criterion |
|---|---|---|---|
| M1 | **Engine core** (this repo, scaffolded now) | — | Full rules engine + Balanced Dice, spec test suite green, seeded replay determinism |
| M2 | **Board generation** (scaffolded with M1) | M1 | Strict-product constraint boards; validator; canonical serialization |
| M3 | **Heuristic baseline agents** (Stage A) | M1, M2 | EV placement evaluator + greedy play policy; random-vs-heuristic win-rate gap demonstrated |
| M4 | **Determinized MCTS engine** (Stage B) — **done: 85% vs heuristic** | M3 | Beats heuristic agent ≥70% ✓ (85/100, CI lower bound 76.7%); emits per-move Q-values ✓; card-tracking module exact absent hidden discards ✓ |
| M5 | **Self-play training loop** (Stage C) — **CRITERION MET; flywheel turned once** | M4 | Net-guided MCTS beats Stage B at equal simulations ✓ (big1 pure: 72/120 = 60%, CI lower bound clears 50%; arc 7.5% → 20.8% → 60% as games went 300 → 5,000). First full flywheel turn (gen-2: 1,500 net-guided games + value dropout): **gen-3 beat incumbent big1 33/60 (55%)** and matched it vs raw — promoted, honestly within noise. Champion: `checkpoints/gen3fly.pt`. Lever 1 done — **gen-4 board-blind value path (`--board-blind-value`) cured value memorization**: val value BCE 0.424 → 0.432 over 8 epochs (was 0.45 → 0.60 unfixed, 0.43 → 0.53 with dropout), best checkpoint = final epoch, best val loss 1.7146 vs 1.7529. Gates: 68/100 vs raw engine (CI-LB 58.3% — first champion to clear 50% decisively), 36/60 (60%) head-to-head vs gen3fly → promoted. Lever 2 done — **lockstep batched leaf evaluation + vectorized encoder: 1.9× net-guided search speedup** (1.53s → 0.80s per evaluate; search results visit-identical by test). Lever 3 done — **gen-5, the first big turn: 3,000 net-guided games (2× gen-3's turn), trained on all 9,500 games; beat incumbent gen4_blind 60/100 (CI 50.2–69.1%) — the first promotion with a CI lower bound above 50%**. Gen-6 (5,000 games with gen-5 in the loop, trained on all 14,500 games): **vs raw 71/100 (CI 61.5–79.0%, best ever); vs gen5 54/100 — promoted, within noise**. **Champion: `checkpoints/gen6.pt`**. Labeling switch shipped (net-guided mining + labeling; v3 set = 1,457 puzzles, 11× v1, incl. new endgame/devcard/trade types; trainer serves v3). Experiment suite: stale-data ablation 49/100 (big_* retired), d=192 probe 49/100 (capacity not the constraint), search-param grid all within noise (defaults survive) → the open lever is target quality. Gen-7 (3,000 games at --sims 400, fresh-data window): **vs raw 75/100 (best ever); vs gen6 114/200 = 57.0% (CI 50.1–63.7%) — firm promotion at the 200-game standard**. **Champion: `checkpoints/gen7.pt`**. Gen-8 (recipe repeat): vs raw 72/100; vs gen7 **218/400 = 54.5% (CI-LB < 50%) — not promoted**; same-size turns at sims 400 are near their asymptote. Gen-9 (the sims-800 turn, scaled to 500 games/this machine's measured throughput — see CLAUDE.md seed ledger): vs raw 67/100 (CI 57.3–75.4%, weaker context than gen7/gen8); vs gen7 **99/200 = 49.5% (CI 42.6–56.4%) — not promoted**, decisively below even the extend-the-gate threshold. **Two consecutive non-promotions on the sims-dial recipe — that lever is exhausted.** Champion remains `checkpoints/gen7.pt`. Next strength gains need a regime change (engine port for cheaper self-play data, true GPU batching, or KataGo-style target tricks) rather than another same-recipe turn; meanwhile shift effort to product (trainer usage → difficulty calibration → UI) |
| M6 | **Puzzle mining + move scoring** — **done** | M4 (better with M5) | Pipeline ✓ (`python -m puzzles.pipeline`): self-play games → candidates (placement/robber/midgame) → 3-seed stable deep labeling → admitted puzzles with per-move point tables (JSONL, engine-free serving). First run: 12 puzzles / 144 candidates / 16 games in 100s; admission dominated by no-clear-best (flat positions correctly rejected) |
| M7 | **Trainer application** — **done** | M6 | Present puzzle → submit move → graded points ✓ (`python -m trainer.server`, http://localhost:8321): SVG click-to-move board, observation-level presentation (answer-leak-proofed), regret-table scoring, puzzle-Elo rating pool, 40% placement mixture; 129-puzzle starter set |
| M8 | **Explanations (deferrable)** | M6 | Template explanations from eval deltas; LLM verbalization later |

---

## 4. Detailed stage plan

### Stage 1 — Engine core (M1) — **scaffolded in this repo**

- **Purpose:** exact, fast, deterministic implementation of the ruleset; everything else is a client of it.
- **Key decisions (made in the scaffold):**
  - Python 3.10+, stdlib-only engine (`dataclasses`, `enum`, `random.Random` injected everywhere). Python is justified by the ML stages (PyTorch ecosystem) and the specs' own Python sketches; if search throughput becomes the bottleneck at M4/M5, port the hot loop (`apply_action`/`legal_actions`) to Rust/C++ behind the same interface — the test suite is the porting contract.
  - **Precomputed static topology** (`engine/topology.py`): the 19-hex/54-vertex/72-edge graph with all adjacency maps built once from axial coordinates; hexes/vertices/edges are dense integer ids → array-friendly state, cheap copies.
  - **Mutable `GameState` + explicit `clone()`** rather than immutable states: MCTS needs fast copy-and-descend; full-dict copies of a small state beat persistent structures in Python.
  - **Flat action space** (`Action` dataclass: type + integer/enum args), `legal_actions(state)` / `apply_action(state, action)` as the entire engine API. Multi-step effects (7-sequence discards → robber → steal; Road Building's two placements) are modeled as *pending sub-decisions* so every decision point is a normal action — exactly what a search tree and a puzzle UI both want.
  - **Two state views** per spec §14: `GameState` (perfect) and `GameState.observation(viewer)` (hides opponent hand composition, opponent dev cards, deck order, dice internals; exposes counts).
  - **Turn order per spec §6:** roll first, then actions — i.e. *no pre-roll knight* (standard Catan allows it; spec's turn structure does not mention it). Marked `[To Calibrate]` against Colonist.
  - Steal in 1v1 is automatic (single opponent), rolled from the state's seeded RNG.
  - Standard piece limits (15 roads / 5 settlements / 4 cities) — `[Verified CATAN]`, spec silent; flagged.
- **Deliverables:** `engine/` package + `tests/` (this repo).
- **Validation:** the spec §16 required-tests list, implemented in `tests/`; plus a full-game smoke test (seeded self-play with a build-greedy random agent) asserting invariants (resource conservation modulo bank, VP accounting, termination or turn-cap without illegal states).
- **Risks:** rules subtleties (longest-road recomputation when a settlement cuts a road; Friendly-Robber fallback set). Each has a dedicated test.

### Stage 2 — Board generation (M2) — **scaffolded with Stage 1**

- **Purpose:** unlimited valid, realistic boards for games and puzzles.
- **Decisions:** rejection sampling for number tokens (constraints are loose enough that acceptance is fast); fixed port *positions* on the coastal ring with the standard spacing pattern, randomized port *types* (matches spec §4.3 step 4); `strict_product_mode` (6/8 and 2/12 non-adjacency) as default with the optional anti-clumping checks behind flags; `validate_board()` usable on externally supplied boards.
- **Deliverables:** `engine/board_gen.py` (generator + validator), canonical `to_dict/from_dict` serialization.
- **Validation:** property tests over many seeds (multisets exact, constraints hold); crafted invalid boards rejected.
- **Risks:** low — rejection rate; measured empirically in tests.

### Stage 3 — Heuristic baseline agents (M3, Stage A of §2)

- **Purpose:** a credible non-ML evaluator: bootstrap for search, opponent for benchmarking, and a shippable placement-puzzle labeler.
- **Key components:** pip-count production expectation per vertex (probability-weighted resource income), resource-mix scoring against build recipes, port/production synergy, expansion-room and road-distance features, robber-exposure and Friendly-Robber-eligibility awareness, LR/LA race features.
- **Deliverables:** `agents/heuristic.py` (placement scorer + greedy turn policy), `agents/random_agent.py`, head-to-head harness.
- **Validation:** heuristic ≫ random (win rate); placement scores sanity-checked against known-good openings (e.g., 6-8-9 wheat/ore spots outrank edge deserts).
- **Risks:** heuristic myopia — acceptable; it is a bootstrap, not the product.

### Stage 4 — Determinized MCTS engine (M4, Stage B of §2) — **built; see README for measured strength**

- **Purpose:** the first real engine; per-move Q-values for puzzle labeling.
- **Key components (as implemented):** card-tracking/belief module (`search/belief.py` — exact opponent-hand inference under the no-trade rule, degrading gracefully only on hidden discards); determinization sampler (public info preserved, hidden hand/devs/deck resampled from belief, dice latent state carried with reseeded streams); **PUCT** with the Stage 3 heuristic as the policy prior (softmax over z-scored action scores — the exact slot the Stage 5 network fills), FPU reduction, chance handled by outcome-keyed children sampling the real `BalancedDice`; DISCARD fan-out capped at internal nodes only; truncated build-greedy rollouts + static win-prob leaf (`search/value.py`, including a build-progress term); terminal values depth-decayed so a win now outranks a win later; per-move aggregation across K determinizations, **visit-primary ranking** (raw-Q ranking lets unvisited actions' neutral 0.5 win in losing positions — a real bug found in play testing).
- **Deliverables:** `search/` package; `MCTSEngine.evaluate(position) -> [MoveEval(action, q, visits)]` — **this interface is the product primitive** and is frozen from here on.
- **Validation:** ≥70% vs Stage 3 heuristic; Q-value stability across seeds on fixed positions (measured: sharp positions agree 5/5 on the top move; flat positions show honest near-ties — exactly what Stage 6 admission filters reject); hand-inspected labels on ~50 curated positions.
- **Risks:** Python search throughput (mitigation: state clone profiling, then native port of the hot loop); determinization artifacts (mitigation: disagreement flagging, §2.5); low-budget play strength vs the tuned heuristic — puzzle labeling is offline and uses much larger budgets than live play.

### Stage 5 — Self-play training (M5, Stage C of §2)

- **Purpose:** strength scaling; better priors → deeper effective search → sharper puzzle labels.
- **Key components:** observation encoder (planes/graph features over the fixed topology + scalar hand/VP/dice-memory features); policy head over the flat action space; value head (win prob); replay buffer of (observation, search policy, outcome/bootstrapped value); Elo ladder vs frozen checkpoints and Stage B/3 anchors. PyTorch.
- **Validation:** monotone ladder progress; net-guided MCTS > raw MCTS at equal simulation budget.
- **Risks:** compute cost, training instability — bounded because Stage B remains the fallback engine (§2.5).

### Stage 6 — Puzzle mining and move scoring (M6)

- **Purpose:** turn the engine into the trainer's content.
- **Realistic position generation:** positions are *mined from engine self-play games* (never synthesized ad hoc), so midgame positions are reachable and plausible by construction; placement puzzles come from fresh generated boards at each of the four draft decision points (A1/B1/B2/A2 — note B2/A2 puzzles include the opponent's visible choices, which is instructive).
- **What makes a good puzzle (admission criteria):**
  - deep evaluation of all legal moves (large simulation budget, many determinizations — offline, so spend freely);
  - **clarity:** Q(best) − Q(2nd) ≥ τ₁ (a real best move exists), *or* an explicit "graded" puzzle type where several moves score well;
  - **stability:** best move invariant across seeds/determinization resamples;
  - **non-triviality filters:** position not already decided (|Q(best)| < 0.95), best move not the only legal move, etc.;
  - store per-puzzle: full position, legal-move list with Q-values, admission metadata, difficulty estimate (Q-gap × depth-to-payoff).
- **Move scoring → points:** score the user's move `m` by regret `Δ(m) = Q(best) − Q(m)` in win-probability space (the Catan analogue of centipawn loss). Map to points with a fixed piecewise table, e.g. Δ=0 → +100; Δ≤0.02 → +75; Δ≤0.05 → +40; Δ≤0.10 → +10; Δ≤0.15 → 0; Δ>0.15 → −25 (blunder). **Normalization across sharpness:** regret is already position-normalized (win-prob units), and admission criteria bound sharpness from below; flat positions never become "find the best move" puzzles. **Ties/near-ties:** any move within ε of the best scores full points (ε ≈ Q-value noise floor measured in Stage 4 stability runs).
- **Explanations (deferrable, per the brief):** near-term — template text from interpretable eval deltas (production lost/gained, LR/LA race impact, robber exposure, port unlocked), computed by diffing feature vectors along the engine's principal line; later — an LLM verbalizing the engine's line + feature diff. Neither blocks scoring; the point tables above depend only on Q-values.
- **Validation:** human review of sampled puzzles; A/B of admission thresholds; regression suite of frozen puzzles re-labeled on every engine upgrade (labels must not churn).

### Stage 7 — Puzzle mixture and trainer application (M7)

- **Mixture:** default session = configurable mix (start 40% placement / 60% post-placement) drawn by difficulty band around the user's running rating (standard puzzle-Elo: user and puzzles share a rating pool; solve fast/correct → user up, puzzle down).
- **App:** thinnest viable slice — engine + puzzles behind a small API (FastAPI), board rendering + move input + point feedback in a web client; puzzle format is JSON from Stage 6, so the UI has no engine dependency. Deliberately unspecified further; it is not the hard part.

---

## 5. Open questions (human decisions)

1. **Pre-roll knight:** spec §6 excludes it (roll happens first); standard Catan allows it. Confirm against Colonist ranked 1v1 behavior — one-line change in the engine either way. `[To Calibrate]`
2. **Colonist board-constraint calibration:** stay in `strict_product_mode`, or invest in scraping/reverse-engineering real Colonist boards for `colonist_calibrated_mode`?
3. **Compute budget for Stage 5** (self-play): a laptop-scale run is useful; a real run wants a GPU box. Decide at M4 exit.
4. **Scoring table constants** (τ₁, ε, point bands): proposed defaults above; tune with real users at M7.
5. **Native-port trigger:** agree on the search-throughput threshold (positions/sec) below which we port the engine hot loop.

## 6. Assumptions made (flagged in code where relevant)

- Standard piece limits (15/5/4) apply — spec silent, `[Verified CATAN]`.
- Discard hand-size check is `> 9` strictly; discard count is `floor(n/2)`.
- On a 7 with both players over the limit, discards resolve in player-index order (simultaneous in reality; order is strategically irrelevant without trading).
- 1v1 steal victim choice is automatic (single opponent); stolen card uniform-random from the seeded RNG.
- Balanced-dice ambiguities resolved as: 7-streak resets on any non-7 roll; "sevens against a player" = sevens rolled on that player's turn; player-imbalance adjustment defaults to 1 until any 7 has been rolled. All isolated in `engine/dice.py` and trivially re-tunable. `[To Calibrate]`
- Bank is infinite (`recommended_engine_mode_v1` per spec §10.4) behind an abstract enough seam to add finite-bank later.
- Road Building requires ≥1 legal placement to play (spec's "safer implementation").
