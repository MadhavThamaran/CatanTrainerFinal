# Prompt for Fable — Plan the 1v1 Catan Tactics Trainer

You are a senior game-AI architect and engineer. This task has **two deliverables**:

1. A rigorous, step-by-step **implementation plan** for the project, including one hard technical recommendation with justification (the ML/search method).
2. A working **scaffold of the engine core** — runnable, tested Python — matching Stage 1 of your own plan.

Think deeply and finish the plan before you start coding, so the scaffold matches the plan.

## 0. First, read the existing specs

Three markdown files in this repository (`/Users/mthamaran/projects/catan-trainer/`) fully define the ruleset. Read all three before planning:

- `rules.md` — the full 1v1 Colonist-inspired rules spec (win condition, board, setup, turn structure, costs, dev cards, robber, trading, longest road / largest army, hidden information, required tests).
- `1v1_Colonist_Inspired_Spec.md` — the same ruleset in tagged/tabular form, distinguishing `[Verified Colonist]`, `[Verified CATAN]`, `[Product Constraint]`, `[Engine Assumption]`, `[To Calibrate]`.
- `balanced_die_rules.md` — the exact Balanced Dice behavior (36-outcome deck without replacement, reshuffle < 13 cards, anti-streak suppression 0.34 over a memory of 5, per-player 7-balancing, final 7 multiplier clamped to [0,2]).

Treat these as the authoritative ruleset. Do not restate them at length in your plan; reference them and build on them.

**Ruleset in one paragraph (for grounding):** 1v1 Catan, 15 VP to win, Balanced Dice, Friendly Robber (can't rob/block a player with ≤2 visible VP), discard threshold >9 cards, **no player-to-player trading** (only 4:1 bank / 3:1 & 2:1 port trades), random board generation enforcing 6/8 and 2/12 non-adjacency, base-game dev deck. It is a game of **imperfect information** (hidden hands, hidden dev cards, unknown dev-deck order, latent dice-controller state).

## 1. Product goal

Build a **Catan tactics trainer**, analogous to the chess.com tactics trainer:

- A position is set up on the board and presented to the user.
- The user must find the **best move**.
- Scoring is **ranked**: the most points for the best move, fewer points for the 2nd-best move, and so on, down to **losing points for a bad move** (analogous to centipawn-loss scoring in chess).
- **Nice-to-have (do not let it block the core):** an explanation for *why* a move is bad. If the scoring approach cannot naturally produce this, defer it — but the plan must still guarantee we can **find and rank the best move**.

The **hard, load-bearing requirement** is therefore a **move evaluator / "engine"**: given a position, it must return the legal candidate moves ranked by quality, with a numeric score per move. Everything else (board generation, position generation, UI, points math) is downstream of this. Note the reframing this implies: like chess needs Stockfish before it can have puzzles, this project needs a strong Catan **engine** first, then generates and labels puzzles with it. Call out in your plan whether you agree with this framing.

## 2. Two puzzle phases

The trainer must, by default, present a **mixture** of puzzle types drawn from two distinct game phases:

1. **Initial placement phase.** Given an (often empty or partially-drafted) board, following the **A→B→B→A snake draft** of settlements + roads, find the best placement. Short horizon, but the choice sets up the entire game (production expectation, port access, road/expansion potential, resource diversity, number probability, blocking).
2. **Post-placement play phase.** Given a realistic mid/late-game position (roads, settlements, cities already built; some VP accrued; robber somewhere), find the best action — build, buy/play a dev card, bank/port trade, robber placement on a 7, or end turn.

Your plan must address **both phases explicitly**, including whether they share one evaluator or need different treatment (e.g. placement is a short-horizon expected-value problem; midgame needs full lookahead).

## 3. The central research question (the hard part) — answer this decisively

Recommend a **machine-learning / search method** for the move evaluator, and justify it against the alternatives. This is the most important part of your deliverable.

Constraints and known pitfalls to address head-on:

- **Imperfect information.** The evaluator must handle hidden opponent hands / dev cards / deck order / latent dice state. Discuss whether to evaluate over information sets, use determinization, or model beliefs.
- **The PPO credit-assignment problem (the user's specific past pain).** Plain model-free RL like PPO struggled here because **many actions elapse between a valuable action and any reward signal** — e.g. attributing a win to a road built 100 actions earlier is hard, sparse-reward, long-horizon credit assignment. Explain *why* a search-based / bootstrapped-value approach (or reward shaping / potential-based shaping / hierarchical RL) does or does not fix this, and let it drive your recommendation.
- **The output must be per-move ranked scores, not just a policy.** The method must naturally yield a rankable quality score for *every* legal move in a position (e.g. MCTS Q-values or visit counts, or an action-value function), because that is exactly what puzzle scoring consumes.

Evaluate at least these families, then recommend one (or a hybrid) with clear reasoning and trade-offs:

- **AlphaZero-style MCTS + learned policy/value network via self-play** (per-move Q-values fall out naturally; value is bootstrapped so there is no long-horizon return-propagation problem). Note that vanilla AlphaZero assumes perfect information — specify the imperfect-information adaptation.
- **Determinized search — PIMC (Perfect-Information Monte Carlo)** and **Information-Set MCTS (ISMCTS)** — sample opponent hands / deck orders, search each, aggregate. Discuss strategy-fusion / non-locality weaknesses.
- **Imperfect-information solvers — ReBeL / Player of Games / CFR-family** — strong theoretically; comment on complexity vs. payoff for this project.
- **Model-free deep RL (PPO / other)** — include it, but confront the credit-assignment issue directly and say whether/where it still has a role (e.g. as a fast rollout/policy prior).
- **Heuristic / expected-value baselines** — especially viable for the placement phase and as a bootstrap/sanity check before any neural net exists.

Deliver a clear recommendation, an honest statement of risk, and a fallback if the primary method underperforms.

## 4. What your plan must contain

Produce a **step-by-step, milestone-based plan** that starts from the rules and iterates outward. At minimum, sequence and detail these stages (add, reorder, or split as you see fit — but justify deviations):

1. **Engine core.** State representation (perfect-state vs. observation-state per the specs), legal-action generation, transition function, Balanced Dice controller, win checking, longest road / largest army, Friendly Robber logic. Reference the "Required tests" section of the specs and treat the engine as needing a test suite before anything is built on it.
2. **Board generation.** Random boards satisfying the strict-product constraints (6/8 and 2/12 non-adjacency, terrain/number/port multisets, optional anti-clumping). Validation and canonical serialization.
3. **Realistic position generation.** How to produce *valid and realistic* positions for both phases — not just legal but plausible (e.g. reached via self-play or a reasonable policy so midgame positions look like real games, and placement boards are interesting/instructive). Define what makes a position a *good puzzle* (a clear best move, meaningful separation between best and alternatives).
4. **The move evaluator / "engine"** per Section 3 — including a simulation/self-play data-generation loop if the recommendation needs training data, evaluation harness, and strength benchmarking.
5. **Move scoring → points.** How to map the evaluator's per-move scores into the ranked point system (best-move max points, graded partial credit for near-best moves, penalties for blunders). Address ties, near-equal moves, and normalizing across positions of differing sharpness. Specify how the (optional) "why a move is bad" explanation could be produced (feature attribution, eval-delta breakdown, or an LLM verbalizing the engine's assessment) and flag it as deferrable.
6. **Puzzle selection & mixture.** How the default trainer mixes placement and midgame/endgame puzzles, and any difficulty/curriculum controls.
7. **Application / UX layer.** Minimal description of how a puzzle is presented, a move is submitted, and points are shown.

For **each** stage include: purpose, key design decisions, concrete deliverables/artifacts, dependencies on prior stages, how it is **tested/validated**, and the main risks.

## 5. Second deliverable — scaffold the engine core (Stage 1) as working code

After the plan is written, **implement a runnable, tested scaffold of the engine core** (Stage 1 above). This is not pseudocode — it must import, run, and pass its own tests.

Requirements:

- **Project layout.** Create a sensible package layout (e.g. `engine/`, `tests/`, `pyproject.toml` or `requirements.txt`, a short `README.md` explaining how to run tests and a demo). Match whatever structure your plan proposes.
- **State representation.** Implement both the perfect-state and observation-state views described in the specs, with clean serialization.
- **Board model + generation.** Hex/vertex/edge graph for the standard board, plus a board generator that enforces the strict-product constraints (6/8 and 2/12 non-adjacency, terrain/number/port multisets). Board generation may live here or be stubbed with a clear interface if you prefer to defer full generation — state which, and make the interface real.
- **Core rules engine.** Legal-action generation, the transition/`apply_action` function, turn structure, costs/build rules, robber + Friendly Robber logic, dev-card timing, longest road / largest army, bank/port trades (no domestic trade), win checking at 15 VP.
- **Balanced Dice controller.** Implement the `DicePolicy` interface with an IID baseline **and** the balanced-dice controller exactly as specified in `balanced_die_rules.md` (36-card deck, reshuffle < 13, recent-roll suppression 0.34 over memory 5, per-player 7-balancing, final 7 multiplier clamped to [0,2]). Make it deterministic under a fixed seed.
- **Tests.** Provide a passing `pytest` suite covering the "Required tests" list from the specs (setup legality & snake order, board 6/8 and 2/12 validation, production, discard >9 only, Friendly Robber visibility, longest-road blocking edge cases, largest-army transfer, dev-card timing, port/trade legality, no-domestic-trade enforcement, hidden VP excluded from visible VP, balanced-dice determinism under fixed seed).
- **Determinism & seeding.** All randomness routed through injectable seeds so games are replayable.
- **Honesty about scope.** Anything you stub rather than fully implement must be marked with a clear `TODO`/`NotImplementedError` and listed in the README. Do not present stubs as complete. Run the test suite and report the actual results — if something fails, say so.

Keep the scaffold clean and idiomatic; it is the foundation every later stage builds on.

## 6. Deliverable format

- Assume Python (the specs already sketch Python enums / a `DicePolicy` interface); state and justify the stack, but keep it standard.
- **Deliverable 1 — the plan:** a single well-structured Markdown document (e.g. `PLAN.md`). Lead with:
  - a **one-paragraph executive summary**,
  - your **ML/search recommendation with justification** (Section 3), and
  - a **milestone roadmap** (an ordered list of phases with rough dependencies and a critical path).
  - Then the detailed per-stage plan (Section 4).
  - End with **open questions / decisions the human must make** and **assumptions you made**.
- **Deliverable 2 — the engine scaffold:** the runnable, tested code described in Section 5, committed to the repo with a `README.md`. Finish and lock the plan first, then build the scaffold to match it.
- Be decisive. Where the specs or this prompt leave a choice open, pick a default, mark it as an assumption, and move on. Do not produce a survey of options with no recommendation.
- After building, **run the test suite and report the real output.** State clearly what is fully implemented, what is stubbed, and what remains for later stages.
