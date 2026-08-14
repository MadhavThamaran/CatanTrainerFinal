# ROADMAP_V2 — after the first eight generations

Context for whoever (and whatever model) picks this up: PLAN.md is the
historical M1–M8 record; this file is forward-looking. Champion is
`checkpoints/gen7.pt` (75/100 vs the raw Stage B engine; gen-8 measured
54.5% vs gen-7 over 400 games — NOT promoted). The trainer serves ~1,450
net-labeled puzzles (v3; a v4 batch of ~1,900 lands per HANDOFF.md) and
has a live play-vs-bot mode. Every claim below cites what was measured.

## A. Making the bot stronger

**Where the strength curve stands.** Head-to-head per-turn gains:
gen5→60%, gen6→54%, gen7→57%, gen8→54.5%. The three cheap levers are
spent: value memorization is fixed (board-blind path, val BCE flat at 2M
samples), inference is lockstep-batched (1.9×), and the experiment suite
returned three clean negatives (dropping stale data 49/100; d=192
capacity 49/100; c_puct/temperature grid all within noise). Gen-7 proved
the one live lever is **target quality** (sims 256→400 gave the last firm
promotion); gen-8 showed repeating a lever gives diminishing returns.

Ranked options, with honest expected value:

### A1. Deeper-target turn at scale (default next turn; high confidence, modest gain)
Gen-9 = gen-7's recipe with the dial turned again: `--sims 800`,
5,000+ games, rolling window (gen6+gen7+gen8+gen9; retire gen5 — the
ablation showed old-generation data goes inert). On the old laptop this
was ~2 days of self-play; scale to the new machine's cores (the selfplay
pool uses `cpu_count()-2` workers). Gate: 100 vs raw + **200 vs gen7**
(promotion = CI lower bound > 50%; the 200-game standard exists because
two promotions were unresolvable at 60–100 games). Seeds: data from
70000, gates from 270000. **Exact commands, scripts, decision table:
`docs/GEN9_RUNBOOK.md`.**

### A2. Port the engine hot loop (the compounding investment)
`legal_actions`/`apply_action` in Rust behind the same interface —
PLAN.md always reserved this; the test suite is the porting contract.
**Full contract: `docs/RUST_PORT_SPEC.md`** (RNG bit-parity, ordering
guarantees, four validation tiers, benchmark gates).
Profiling showed the Python engine is ~10–15% of net-guided search but
100% of rollouts/mining games. A 10–50× hot loop makes every future turn,
gate, and labeling run cheaper — it compounds like the lockstep fix did.
Do it when a big machine makes self-play the clear bottleneck again.

### A3. GPU/MPS batched inference (only with a real batch source)
Batch-1..4 inference on CPU beat GPU dispatch overhead on the old
machine. It pays only with bigger batches: lockstep across MORE
determinizations (k=8 → batch 8), or a central inference server batching
across the selfplay pool's workers (bigger refactor). If the new laptop
has a strong GPU, measure first: `evaluate()` wall time at k=4 CPU vs GPU
before committing to any refactor.

### A4. Search-quality experiments the grid didn't cover
- **ISMCTS / shared tree across determinizations** — PLAN flagged it as
  the upgrade if strategy-fusion artifacts appear; nobody has looked for
  them since. Cheap study: on ~50 labeled puzzles, compare per-move Q
  across determinizations; high inter-world best-move disagreement =
  fusion evidence.
- **Playout cap randomization / KataGo-style tricks** in selfplay
  (visit-count temperature schedules, forced playouts) — literature says
  these sharpen policy targets at fixed sims. Medium effort, real upside
  given target quality is the binding lever.
- **Finite bank + pre-roll knight calibration** (PLAN §5 open questions)
  — rules-fidelity items; matter only if the target is literal Colonist
  parity. `[To Calibrate]` markers sit in `engine/dice.py` and rules.

### A5. What NOT to redo
Stale-data ablation, capacity probe at this data size, c_puct/prior-temp
grid, dropout-vs-board-blind — all measured, all resolved. The reports
live in `data/*_report.txt` and README's Stage-5 section.

## B. Making the puzzles better

### B1. Difficulty recalibration from real solves (blocked on usage)
`difficulty` is a Q-gap proxy and skews hard (1,097/322/38 in v3). Once
accounts exist and people solve puzzles, replace it: fit predicted solve
probability from puzzle-Elo attempts (the `Ratings` log already records
every attempt), re-band easy/medium/hard by realized solve rates, and
feed session mixing. ~Half a day once ~500 attempts exist.

### B2. Explanations (M8) — the biggest product gap
Puzzles say WHAT the best move is, not WHY. The designed approach
(PLAN §Stage 6): diff interpretable features (production pips gained,
port unlocked, LR/LA race deltas, robber exposure) between the best move
and the runner-up; render from facts, never prose-first. **Full spec:
`docs/EXPLAIN_SPEC.md`** — key insight: no search needed, so the existing
~3,300 puzzles get explanations from a minutes-long annotation pass.

### B3. Labeling engine upgrade discipline
Labeling runs on gen-6; gen-7 is one within-noise step ahead — not worth
a relabel churn. Rule: relabel the live set only when the champion beats
the LABELING engine by CI-LB > 50% at 200 games (i.e. two+ promotions
accumulated), then run `scripts/relabel_check.py --net <champ>` on a
30-puzzle sample and hand-review every BEST-MOVE-CHANGED before
committing. The road-followup deep-label fix (full sims, 3 seeds) is
already in.

### B4. More content, targeted
Mining now tags endgame/devcard/trade positions. v3+v4 ≈ 3,300 puzzles is
plenty for launch; the next mining run should wait for B1's data on which
categories users actually find instructive (endgame races had only 72 —
if they rate well, mine specifically for them with a VP-threshold filter).

## C. Making play mode better

- **Post-game review — the killer feature.** After a game, run the
  labeling engine over the game's decision points (the session already
  logs every action) and show a chess.com-style report: your blunders by
  regret, the engine's line at each. All the pieces exist: `MCTSEngine`
  deep evaluate + regret scoring + the board UI. ~A day of work; huge
  differentiation.
- **Difficulty settings** — bot strength dropdown mapping to
  (checkpoint, sims): gen2/64 = casual, gen5/160 = club, gen7/400 = max.
  Trivial to wire (`PlayService` already takes both).
- **Polish backlog**: piece-placement pop/slide animations (pieces
  currently appear via full re-render), dice-roll sound toggle, "hint"
  button (one engine evaluate at play budget — reuses everything),
  rematch with same board, game history in the account once auth exists.

## D. Product vision — "everything you need to get better"

The proven improvement loop (chess.com/Chessable convergence): play →
be told what went wrong → drill exactly that → play again. We own the
hard parts (evaluator + content pipeline); the missing features, ranked:

1. **Weakness dashboard** (measure): per-skill Elo, review blunder
   taxonomy, regret economics, every leak ending in a "Drill this"
   button. **Full spec: `docs/DASHBOARD_SPEC.md`** — note its attempt-log
   enrichment prerequisite, which should land EARLY (every attempt
   logged before it is a thin data point forever).
2. **Placement lab** (train): endless generated boards, place all four
   draft picks, engine-graded with facts-based explanations, longitudinal
   stats on placement biases. **Full spec: `docs/PLACEMENT_LAB_SPEC.md`.**
3. **Coach mode** (play): live pre-move interjection on mistakes — the
   engine stops you BEFORE a bad move applies (takebacks are rejected:
   every post-hoc undo leaks hidden info or rewinds chance). **Full
   spec: `docs/COACH_SPEC.md`.**
4. **Bot ladder** (play): 8 named rungs from the checkpoint archive,
   unlocks, stars, a play-Elo, offline calibration script. **Full spec:
   `docs/LADDER_SPEC.md`.**
5. **Curriculum lessons** (train): six ordered tracks, in-repo markdown
   content + tag-filtered unrated drill sets + pass bars. **Full spec:
   `docs/CURRICULUM_SPEC.md`.**
6. **Spaced repetition on missed puzzles** (train): Leitner boxes over
   rated misses, Elo-isolated reviews, ~0.75 day. **Full spec:
   `docs/SRS_SPEC.md`.**
7. **Analysis board** (power tool): explore from any played-game or
   puzzle position with perfect info and CHOOSABLE dice; no manual editor
   in v1. **Full spec: `docs/ANALYSIS_SPEC.md`.**
8. **Retention layer** (needs accounts): daily puzzle, puzzle rush,
   streaks, leaderboards.

**Non-goal:** human-vs-human multiplayer — different product, no engine
leverage, Colonist already owns it.

Suggested build order after HOSTING ships: 6 (days) → 1 (the dashboard,
~2-3 days once review data flows) → 3+4 (coach + ladder, ~2 days) →
2 (placement lab, ~3 days) → 5/7/8 as appetite dictates.

## E. Sequencing recommendation (new machine)

1. HANDOFF.md steps → repo cloned, tests green.
2. HOSTING.md → accounts + public puzzle trainer (unlocks B1 data).
3. C's post-game review (product wow while training runs in background).
4. A1 gen-9 turn in the background throughout.
5. B2 explanations.
6. Reassess: if gen-9 promotes firmly, consider B3 relabel + A2 port; if
   it doesn't, the engine has hit this recipe's ceiling — shift fully to
   product until A2/A3 change the compute regime.
