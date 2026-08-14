# ANALYSIS_SPEC — the analysis board ("what if?")

The power tool: take any position you've reached, ask the engine what it
thinks, try lines, branch, compare. Chess players live in this tool;
here it has one Catan-specific superpower — **you choose the dice**.

## 1. Scope decision (the big one)

**v1 has NO manual position editor.** Every analysis session starts from
a position the system already trusts:
- any decision in a played game (game records replay deterministically —
  "🔬 Analyze" on a review row or a game-log entry),
- any puzzle position ("Analyze" on a puzzle result screen).

Rationale: a from-scratch editor drags in the full legality surface
(distance rule, connectivity, hand/bank conservation, phase consistency)
for a niche payoff; "explore from a real position" covers the actual use
case — *what should I have played here, and what happens if…* Manual
editing is v2, if ever.

## 2. Information model (decided)

Analysis shows **perfect information** — both hands, dev cards, deck
count — exactly like chess analysis shows both sides. This is post-hoc
study, not play; the stored states (game records, puzzle states) are
perfect-view already. The UI gets a "revealed" opponent panel with a
distinct tint so it never gets confused with the hidden-hand play view.
(The REVIEW feature grades fairness-constrained — what you could have
known; the analysis board answers a different question and says so in a
tooltip: "engine sees all hidden info here".)

## 3. The Catan-specific superpower: chance control

Applying a ROLL in analysis doesn't sample — it ASKS:
- a 2–12 picker annotated with the true balanced-dice probabilities for
  that state (the simulator exposes its adjusted distribution — a
  fidelity feature no generic tool has),
- "random" button for honest sampling (resampleable).
Steals: picker over the victim's actual (revealed) hand. Dev-card draws:
picker over the remaining deck composition.

Engine seam required: `engine/dice.py` already has a `Scripted` policy —
add a small helper `force_next_roll(state, total)` that swaps in a
one-shot scripted policy (and equivalents for steal/draw via seeded
choice injection). This is the ONE engine-touching change in the spec;
it must come with determinism tests (forcing then replaying produces the
forced outcome; unforced behavior unchanged).

## 4. Session & tree model

- `AnalysisService` sessions (max 4, LRU): each holds a node tree —
  `{node_id: {state, parent, action_taken, children: {...}}}` capped at
  200 nodes/session (evict → error "line too deep, start a new
  analysis" rather than silent pruning).
- Node ids are content-addressed (hash of parent id + action) so
  re-exploring the same line reuses nodes and cached evals.
- Eval cache per node: `MCTSEngine(simulations=ANALYSIS_SIMS=512,
  determinizations=6, seed=fixed, net=<champion>).evaluate(state)` —
  top-8 moves with Q + visits. Synchronous with a spinner (~3–8 s once
  per node; cached after). `--analysis-sims` server flag.
- Determinization note: analysis states are perfect, so the determinizer
  degenerates to the true state — analysis Q-values are cleaner than
  play/review ones. Worth a UI tooltip.

## 5. API

- `POST /api/analysis/new {source: "game"|"puzzle", id, index?}` →
  `{analysis_id, root: <node view>}` (game source: replay the record to
  decision `index`; puzzle source: load the stored state).
- `POST /api/analysis/eval {analysis_id, node}` → `{lines: [{codec_id,
  label, q, visits}...]}` (cached).
- `POST /api/analysis/apply {analysis_id, node, codec_id, forced?:
  {roll: 8} | {steal: "wheat"} | {draw: "knight"}}` → `{node: <new node
  view>}`.
- `GET /api/analysis/tree {analysis_id}` → breadcrumb structure for the
  move list.
- Node view = the play-mode view shape (board, context, legal moves)
  plus `revealed: {opp_hand, opp_devs, deck}`.

## 6. UI

- Entered contextually ("🔬 Analyze" buttons on review rows, game log,
  puzzle results) — no top-level mode button needed in v1.
- Layout: board center; right panel swaps to: revealed-hands card,
  **engine lines** card (top moves, click-to-play, Q bars), **move
  list** card (breadcrumbs of the current line, click to jump back;
  branches indented one level — full tree UI is overkill, a line list
  with fork markers suffices).
- Chance picker modal on ROLL/steal/draw applications.
- "Engine move" button plays the top line one step.
- ~250 lines client — the largest UI item in the product plan; every
  board/marks/table idiom is reused.

## 7. Tests (`tests/test_analysis.py`)

- Source fidelity: analysis root from game record index K equals the
  replayed state at K (VP/hands/board identical).
- Forced chance: `force_next_roll` yields the chosen total exactly
  once, then normal behavior; steal/draw pickers apply the chosen
  outcome; unforced apply still samples (engine determinism tests
  untouched — run the full engine suite).
- Tree: content-addressed node reuse; 200-node cap errors cleanly;
  jump-back serves the stored state.
- Eval caching (one evaluate per node via monkeypatched counter).
- Revealed panel data matches the perfect state.

## 8. Effort & sequencing

Engine seam ~40 lines + tests; service ~180; API ~30; client ~250;
tests ~120 → **~2 days**. Depends on game records (shipped) and is
worth sequencing AFTER review (its natural entry point is a review
row). It also quietly becomes the debugging tool for every future
engine question — expect it to pay for itself internally.
