# catan-trainer

A tactics trainer for **1v1 Colonist-variant Catan** (15 VP, Balanced Dice, Friendly
Robber, no player-to-player trading) — the Catan analogue of the chess.com puzzle
trainer. See **`PLAN.md`** for the full roadmap and the ML/search recommendation;
this repo currently contains **Stages 1–4 of that plan: the rules engine core, the
board generator, the heuristic baseline agents, and the determinized-MCTS move
evaluator** (the product primitive: ranked per-move Q-values).

**Moving machines / what's next:** `docs/HANDOFF.md` (leave-this-laptop
checklist + git push), `docs/HOSTING.md` (public deployment with
accounts), `docs/ROADMAP_V2.md` (bot + product plans with measured
context).

The authoritative ruleset lives in `docs/`:
`docs/rules.md` · `docs/1v1_Colonist_Inspired_Spec.md` · `docs/balanced_die_rules.md`

## Layout

```
PLAN.md               implementation plan + ML/search recommendation
docs/                 ruleset specs (authoritative) + planning prompt
engine/               the rules engine (stdlib-only, Python 3.10+)
  types.py            enums + rule constants (costs, multisets, thresholds)
  topology.py         static 19-hex / 54-vertex / 72-edge graph, coast, port slots
  board.py            Board (terrain / numbers / ports) + serialization
  board_gen.py        strict_product_mode generator + validator (6/8 & 2/12 rules)
  dice.py             DicePolicy: IID, Balanced (per docs/balanced_die_rules.md), Scripted
  state.py            GameState (perfect view) + per-player observation view
  actions.py          flat Action space (every decision point is one Action)
  longest_road.py     longest-road computation with opponent-building breaks
  rules.py            legal_actions(state) / apply_action(state, action)
  game.py             new_game(seed) — fully deterministic from (seed, action log)
agents/               Stage 3 baseline policies
  base.py             Agent interface (begin_game/observe lifecycle hooks)
  heuristic.py        pip-weighted placement scorer + greedy play policy
  random_agent.py     uniform-random baseline
  greedy_random.py    build-prioritizing baseline (tougher than uniform)
  harness.py          head-to-head play_game / evaluate (VP tiebreak at cap)
search/               Stage 4: the move evaluator (product primitive)
  engine.py           MCTSEngine.evaluate(state) -> ranked MoveEval list
  mcts.py             UCT with outcome-keyed chance children, depth-decayed wins
  determinize.py      sample hidden worlds consistent with the viewer's info set
  belief.py           CardTracker: exact opponent-hand inference (no-trade variant)
  value.py            static win-prob leaf evaluator
puzzles/              Stage 6: puzzle mining, labeling, scoring
  schema.py           Puzzle/PuzzleMove + JSONL (codec-id move keys)
  mining.py           candidates from engine self-play (placement/robber/midgame)
  labeling.py         deep multi-seed labeling + admission criteria
  scoring.py          regret -> points table (100/75/40/10/0/-25, tie eps)
  pipeline.py         python -m puzzles.pipeline --games N --out set.jsonl
examples/random_game.py        full seeded game with a build-greedy random agent
examples/benchmark.py          heuristic vs random/greedy win-rate report
examples/strength_benchmark.py MCTS vs heuristic (M4 exit criterion)
tests/                pytest suite (spec §16 tests + agents + search)
```

## Run it

Requires [uv](https://docs.astral.sh/uv/) (it provisions Python 3.12 automatically):

```sh
uv run pytest                             # test suite
uv run python -m examples.random_game 0   # play a full seeded game
uv run python -m examples.benchmark 60    # heuristic vs random win rate
```

Current test status: **70 passed** in ~3s (plus slow-marked strength tests:
`uv run pytest -m slow`). Example games run to a 15-VP winner.

Measured strength ladder (alternating seats, real runs):
- **Heuristic vs uniform random: 400/400 (100%)**, every win by reaching 15 VP;
  no stalls, no cap-decided games.
- **Heuristic vs build-greedy random: 998/1000 (99.8%)**. Both losses are
  opponent dev-card variance — on each of those boards the heuristic wins
  40/40 against other opponent seeds.
- **Heuristic self-play**: 200 games, 0 illegal moves, every game a real 15-VP win.
- **MCTS engine vs heuristic: 85/100 (85%, Wilson 95% CI 76.7–90.7%)** at the
  default play config (`simulations=160, determinizations=4, rollout_depth=0`)
  — the CI lower bound clears the ≥70% M4 exit criterion. Perfectly seat-
  balanced (84% as first player, 86% as second). Run it yourself:
  `uv run python -m examples.mcts_eval --games 100` (parallel, ~4.5 min on
  8 cores). Q-values on sharp positions agree on the top move 5/5 across
  seeds; flat positions show honest near-ties (which Stage 6 puzzle
  admission rejects by design).
- **How the engine wins:** award dominance — in its wins it holds Longest
  Road 65/85 and Largest Army 59/85 with ~3.5 cities to the heuristic's 1.9;
  its losses are the mirror image (heuristic takes LR 12/15). Two boards
  (seeds 44, 72) remain systematically losing at this budget — placement
  traps whose best spots leave a resource hole; earmarked for the Stage 5
  value net.

## Engine API

```python
from engine import new_game, legal_actions, apply_action

state = new_game(seed=0)              # setup phase, snake draft A-B-B-A
while actions := legal_actions(state):
    apply_action(state, actions[0])   # mutates; state.clone() to branch
state.observation(player)             # hidden-info view for policies/UI
```

Everything — discards on a 7, the robber move, Road Building's free
placements — is an ordinary `Action`, so a search tree and the puzzle UI
consume one uniform interface.

## Fully implemented

- Complete turn/action rules: builds, costs, piece limits, dev cards (timing:
  no same-turn play, one non-VP card per turn), maritime trade only (4:1 / 3:1 / 2:1
  via port ownership), discard >9 (floor half), win at 15 VP on own turn
- Friendly Robber (visible-VP ≤ 2 protection, hidden VP excluded, fallback
  destination rule), robber production blocking, 1v1 auto-steal
- Longest Road (opponent settlements cut paths; tie → holder retains; award
  recomputed on settlement placement) and Largest Army (tie → holder retains)
- Balanced Dice exactly per `docs/balanced_die_rules.md` (36-card deck,
  reshuffle < 13, 0.34 recent-roll suppression over memory 5, per-player
  7-balancing clamped to [0, 2]); deterministic and clonable under seed
- Board generation in `strict_product_mode` (6/8 and 2/12 non-adjacency,
  exact multisets) with validator and serialization
- Setup phase (A-B-B-A, distance rule, incident setup road, second-settlement
  starting resources)

## Stubbed / deferred (marked with TODO in code)

- **Optional anti-clumping board constraints** (`engine/board_gen.py::_quality_ok`)
  — rules.md §4.2 "optional recommended" checks; seam exists, returns True.
- **GameState.from_dict / dice-state serialization** — perfect-state `to_dict()`
  works; exact replay is via `(seed, action log)` instead. (`engine/state.py`)
- **Finite-bank parity mode** — bank is infinite per the spec's
  `recommended_engine_mode_v1`; the hook point is `rules.py::_pay`.

## Deliberate rule interpretations (flagged in PLAN.md §6)

- No pre-roll knight (spec's turn structure rolls first) — `[To Calibrate]`
- Balanced-dice ambiguities (7-streak reset, "sevens against" = roller's turn)
  resolved as documented in `engine/dice.py` — `[To Calibrate]`
- Standard piece limits 15/5/4 (spec silent) — `[Verified CATAN]`

## Agents (Stage 3)

`agents.HeuristicAgent` is a greedy, no-lookahead policy whose position
features — pip-weighted production, resource diversity, port synergy, robber
value, opening complementarity, and Longest-Road / Largest-Army contesting —
are the same ones the Stage 5 value net will subsume. It serves three roles:
the initial-placement puzzle labeler, the benchmark opponent, and the
leaf/rollout policy for the Stage 4 MCTS engine.

```python
from agents import HeuristicAgent, placement_value, evaluate, RandomAgent
placement_value(state.board, vertex)   # rank a settlement spot
```

## The move evaluator (Stage 4)

```python
from search import MCTSEngine

engine = MCTSEngine()                    # or bigger budgets for labeling
for m in engine.evaluate(state):         # ranked best-first
    print(m.action, m.q, m.visits)       # q = win prob for the player to act
```

Architecture (PLAN.md §2): root-level determinization (K sampled worlds
consistent with the actor's information set, exact card-tracking belief when
playing full games) × PUCT search with the Stage 3 heuristic as policy prior
(the slot the Stage 5 network will fill), outcome-keyed chance children
sampling the real Balanced Dice, static win-prob leaf evaluation, and
visit-primary move ranking. `Q(best) − Q(move)` is the regret that Stage 6
converts to points.

Findings from strength iteration (each measured, including one refuted):
rank by visits, not raw Q (unvisited actions' neutral 0.5 otherwise wins in
losing positions); z-score heuristic scores before the prior softmax (raw
scales differ per decision type); pure static leaves beat truncated
random-greedy rollouts at equal wall time; terminal values need depth decay
so winning *now* outranks winning later; the value function MUST carry
expansion features — without them roads are invisible to a static leaf and
no amount of extra search compensates (doubling the budget moved 67% → 62%;
adding expansion terms moved it to ~75%); and per-resource production must
be **concave** — a linear pip sum places into pip-rich monocultures (every
systematic-loss board had a near-zero resource column: wood 0 / brick 0 /
ore 1 / wheat 1), while plain resource *weighting* without concavity
measurably fixed nothing (2/36 on the hard boards vs 14/36 with concavity).

## The policy/value net (Stage 5 — infrastructure done, honest status)

`net/` is the complete AlphaZero-style loop: action codec (370-way policy
space; discards excluded by design), actor-perspective encoder (871
features), residual-MLP policy/value net, parallel self-play generation
(visit-distribution targets, mixed z/rootQ values, Dirichlet root noise),
training, and full search integration:

```sh
uv run python -m net.selfplay --games 250 --out data/gen0.npz
uv run python -m net.train --data data/gen0.npz --out checkpoints/gen1.pt
uv run python -m examples.net_eval --net checkpoints/gen1.pt --games 60
```

```python
from net import net_engine   # measured-best ensemble calibration
engine = net_engine("checkpoints/gen2.pt", seed=0)
```

**Measured status (all real runs, equal simulation budgets vs the raw
engine):** gen-1 pure net 6/60 (10%) — sane knowledge (right values on
decided positions, 94% policy mass on a winning city) but it *underexpands*
(5 roads vs 26 over two games): road timing is contextual and 36k samples
can't teach it, while the heuristic prior computes it directly. Gen-2 (86k
samples, wider net): pure 37.5%, ensembled with heuristic priors + static
value 28/56 ≈ parity.

**Gen-3 = the four data-quality fixes** (clean noise-free visit-floor-pruned
policy targets; ×12 dihedral symmetry augmentation — `net/symmetry.py`,
verified as rules automorphisms by test; relational encoder features
[per-vertex placement value, placeability/network flags, open-spot counts];
300 games at 400 sims instead of 600 at 160): policy targets sharpened
(0.50 → 0.61 top-mass before pruning), **val policy CE 1.69 → 1.34 on half
the raw samples** — the fixes demonstrably fixed what they targeted. Match
level: ensemble **41/80 ≈ parity** (pure 3/40: sharp priors + a weak value
head lock the search in confidently wrong lines off-distribution).

**Gen-4 = auxiliary targets + graph network.** Self-play now records dense
final-outcome supervision per position (final VPs of both players, LR/LA
ownership) and `GraphPolicyValueNet` does message passing over the actual
hex/vertex/edge board graph with a structured policy head (road logits from
edge embeddings, etc.) — equivariant to all 12 board symmetries by
construction (verified by test), the inductive bias the flat MLP lacked for
road/expansion context. Same encoder, data format, and evaluator; select
with `--arch gnn`. On identical data the GNN posts the best policy CE yet
(**1.30** vs MLP 1.38, with 4.6× fewer parameters), and pure-net match play
improved 7.5% → 20.8%.

**The 5,000-game run (gen-big) confirmed the game-count thesis.** 698,022
samples from 5,000 games (16 h, zero failed games, `scripts/big_run.sh` —
resumable chunks). Trained `--arch gnn --d 128 --rounds 4`: best-ever
policy CE (1.20), and the game-split validation caught genuine **value
memorization** (each game's unique board fingerprints its outcome; val
value BCE diverged 0.45 → 0.60 across epochs, so best-checkpoint selection
kept epoch 0 — regularizing the value head is the top next-gen training
fix). The gate, fresh seeds, equal sims vs the raw engine:

- **pure net (no heuristic priors, no static value): 72/120 = 60%,
  95% CI 51.1–68.3% — the CI lower bound clears 50%: the M5 exit
  criterion (net-guided MCTS beats Stage B at equal simulations) is MET.**
  The full arc is 7.5% → 20.8% → 60% as games went 300 → 300(GNN) → 5,000.
- ensemble calibration: 53/100 — the blend is now obsolete; the net
  carries the weight itself (retire `net_prior_mix` toward 1.0).

**The AlphaZero flywheel is unlocked**: the net-guided engine generates the
next generation's data (`--net checkpoints/big1.pt`), which was never
viable before.

**Gen-3 (first full flywheel turn):** 1,500 net-guided games (gen-2) +
value-head dropout, trained on all 6,500 games (~923k samples,
`scripts/gen3_after.sh` ran the whole train→gate pipeline unattended).
Results: dropout *halved* the value-memorization divergence (0.43→0.53 vs
0.45→0.60) without curing it; **gen-3 beat the incumbent big1 head-to-head
33/60 (55%)** and matched it vs the raw engine (55/100 vs 60% — overlapping
CIs). Champion: `checkpoints/gen3fly.pt`, promoted on the head-to-head win,
honestly noted as within noise at 60 games. The flywheel turns, but one
1,500-game turn buys a small step — per-generation gains at laptop scale
are real but modest. Next levers, in order: exclude board-identity features
from the value path (the memorization fix stronger than dropout), inference
speedup (MPS/batched leaves) to make generations cheaper, then bigger
turns. **Labeling still uses the raw Stage B engine**: the nets' edge over
it is not statistically firm, and raw labeling is ~25× faster — switch only
when a champion clears it decisively (then audit with
`scripts/relabel_check.py`).

**Gen-4 = the board-blind value path (`--board-blind-value`) — value
memorization cured.** The value/aux heads no longer read the trunk/global
node; they read `_BlindValueFeatures` (`net/model.py`): occupancy-weighted
board interactions (robber-adjusted production pips per resource per
player, robber-blocked pips, reached ports) plus the global scalars, derived
from the existing encoder vector by fixed transforms — so all existing
self-play data trains it unchanged, and an unbuilt hex's identity (the
per-game fingerprint) *cannot* reach the outcome-supervised path
(bit-identical-value property test in `tests/test_blind_value.py`). On the
gen-3 recipe (same 923k samples, `--arch gnn --d 128 --rounds 4 --epochs
8`): val value BCE went **0.424 → 0.432 over 8 epochs (drift +0.008)** vs
0.45 → 0.60 with no fix and 0.43 → 0.53 with dropout-only; best-checkpoint
selection kept **epoch 7** instead of retreating to epoch 0, best val loss
**1.7146 vs gen-3's 1.7529**, and val policy CE set a new best (**1.2825**).
Gates (fresh seeds, equal sims): **pure net vs raw Stage B engine 68/100
(95% CI 58.3–76.3%)** — the first checkpoint whose CI lower bound clears
50% by a wide margin (prior champions: big1 60%, gen3fly 55%) — and
**head-to-head vs incumbent gen3fly 36/60 (60%, CI 47.4–71.4%)**. Promoted
on the head-to-head win (same AZ promotion rule as gen-3, same
within-noise-at-60-games caveat). **Champion: `checkpoints/gen4_blind.pt`.**

**Inference speedup (M5 lever #2): 1.9× on net-guided search, exactly
equivalent.** Profiling showed 85% of search wall time inside the net
evaluator, dominated by *dispatch overhead* on batch-of-1 GNN forwards
(~44 tiny linears each), with encoding another 15% — MPS would make
batch-1 worse, so instead (a) `MCTSEngine` now steps its K determinized
trees in lockstep and batches their leaf evaluations into one forward per
step (`NetEvaluator.evaluate_batch`; 4× fewer forwards at k=4), and (b)
the encoder's per-vertex rules-predicate loops were replaced with
occupancy-driven array ops (bit-equal by test). Per-tree search is
unchanged — `tests/test_lockstep.py` pins visit-identical results against
the sequential loop. Measured: 1.53s → 0.80s per `evaluate()` at the play
config on midgame positions; self-play generations and gates cost roughly
half what they did.

**Gen-5 = the first big flywheel turn (M5 lever #3).** 3,000 net-guided
games with gen4_blind in the loop (`scripts/gen5_run.sh`, seeds
30000-32999, ~3h/500 games post-speedup), trained board-blind on all 9,500
games (~1.35M samples, 10 epochs — val value BCE held a flat 0.426-0.435
band the whole way; best checkpoint epoch 3). Gates
(`data/gen5_report.txt`): vs raw engine 63/100 (CI 53.2-71.8%); vs
incumbent gen4_blind **60/100 (95% CI 50.2-69.1%) — the first promotion
whose head-to-head CI lower bound clears 50%** (gen-3 and gen-4 were
within noise at 60 games; this ran 100). **Champion:
`checkpoints/gen5.pt`.** The doubled turn size bought a real, measurable
step; labeling stays on the raw Stage B engine for now (gen-5's vs-raw
point estimate, 63%, did not extend gen-4's 68% — switch when a champion
clears raw decisively AND `scripts/relabel_check.py` shows stable labels).

**Gen-6 = the second big turn: 5,000 games with gen-5 in the loop**
(`scripts/gen6_run.sh`, seeds 40000-44999), trained board-blind on all
14,500 games (~2.06M samples; val value BCE flat at 0.377-0.388 through
all 10 epochs — the blind path holds at 2M scale). Gates
(`data/gen6_report.txt`): **vs raw engine 71/100 (CI 61.5-79.0%) — best
ever** (arc: 60% → 68% → 63% → 71%); head-to-head vs incumbent gen5
54/100 (CI 44.3-63.4%) — a win, promoted per the AZ rule, but within
noise: per-turn head-to-head gains are flattening (60% → 54%) even as
vs-raw strength climbs. **Champion: `checkpoints/gen6.pt`.** The 71%
CI-LB-61.5% vs-raw result fires the labeling-switch trigger — next step
there is teaching `puzzles/labeling.py` to take a net and auditing with
`scripts/relabel_check.py` before any relabel.

**The labeling switch (Stage 6 meets the flywheel).** `puzzles/labeling.py`,
`puzzles/mining.py`, `puzzles/pipeline.py`, and `scripts/relabel_check.py`
now take a `--net` checkpoint: mining games are played net-guided and deep
labeling searches net-guided (provenance recorded in each puzzle's
`label_config.net`). The gen-6 relabel audit (`data/relabel_audit_gen6.txt`)
found only 5/20 old raw-labeled puzzles survive net relabeling — 13 fell to
no-clear-best (the stronger engine sees flatter truth), 1 flipped best move
— so the v1 set is being replaced wholesale by a freshly mined, net-labeled
v3 set rather than patched. Mining now also tags **endgame / devcard /
trade** candidates (race calculation, dev-card timing, trade lines) with
per-game caps alongside placement/robber/midgame.

**The post-gen-6 experiment suite (`scripts/exp_queue.sh`) — three clean
negatives that set the gen-7 recipe.** (1) *Stale-data ablation*: the gen-6
recipe without the 5,000 heuristic-era `big_*` games gated 49/100 vs gen6 —
the old data contributes nothing; future training uses a rolling
net-guided-only window. (2) *Capacity probe*: d=192 (2.25× params) matched
gen6's val loss (1.7387 vs 1.7380) and gated 49/100 — capacity is not the
constraint; d=128 stays. (3) *Search-param grid*
(`scripts/param_grid.py`): c_puct {1.0, 2.25} and prior temperature
{0.7, 1.4} all within noise of the defaults at 60 games each — the
defaults survive. Elimination leaves ONE open lever: **target quality**,
so gen-7 (`scripts/gen7_run.sh`) plays its 3,000 games at `--sims 400`
(vs 256) and its head-to-head gate runs 200 games (gen-6's 54/100 was
unresolvable at 100).

**The v3 puzzle set — the labeling switch shipped.** 240 gen-6-guided
mining games → 10,044 candidates (the broadened tags yield ~42/game) →
net-labeled at sims=960/dets=8 → **`data/puzzles_v3.jsonl`: 1,457
puzzles, 11× the v1 set** (326 placement, 607 robber, 159 midgame, 150
devcard, 143 trade, 72 endgame; admission 14.5%, rejections dominated by
correctly-filtered flat and cross-seed-unstable positions). The trainer
serves v3 by default. Difficulty skews hard (1,097/322/38) — the Q-gap
proxy wants recalibration against real puzzle-Elo data once the trainer
has users. **v4 followed (360 gen-7-guided games → 1,956 puzzles, 12.9%
admission, deep road labels from birth); merged as
`data/puzzles_v5.jsonl` = 3,413 puzzles, now the trainer default**
(812 placement, 1,394 robber, 371 midgame, 337 devcard, 332 trade,
167 endgame).

**Gen-7 = the target-quality turn — the experiment-driven recipe paid.**
3,000 games at `--sims 400` (deeper visit targets) with gen-6 in the loop,
trained on the fresh-data window only (gen2+gen5+gen6+gen7, big_* retired).
Gates (`data/gen7_report.txt`): **vs raw engine 75/100 (CI 65.7–82.5%) —
best ever** (arc: 60 → 68 → 63 → 71 → 75); **head-to-head vs gen6 114/200
= 57.0% (CI 50.1–63.7%) — the CI lower bound clears 50% at the new
200-game gate standard.** Champion: **`checkpoints/gen7.pt`**. Every
choice in this recipe came from a measured experiment: sims from the
elimination (data-staleness x, capacity x, search-params x → target
quality ✓), the data window from the ablation, the gate size from gen-6's
unresolvable 54/100.

**Gen-8 = the recipe repeated — and the first non-promotion under the
200-game standard.** Same turn as gen-7 (3,000 games at `--sims 400`,
rolling window now gen5–gen8, gen2 rolled out). Gates: vs raw 72/100 (CI
62.5–79.9%, same band as gen-7's 75); head-to-head vs gen7 111/200, and
after a 200-game extension **218/400 = 54.5% (CI ≈ 49.6–59.3%) — the CI
lower bound does not clear 50%, so gen-8 is NOT promoted. Champion
remains `checkpoints/gen7.pt`.** Gen-8 is probably marginally stronger,
but the honest read is that same-size turns at this sim budget are
approaching their asymptote (head-to-head arc: 60% → 54% → 57% → 54.5%);
the banked gen8 data stays in the window for whatever comes next. Step
changes worth considering before another same-recipe turn: much deeper
self-play (sims 800+, ~2× turn cost), a larger combined turn, or shifting
effort to the product side until there's a reason to want more Elo.

**The large-run recipe.** Measured rate: ~18 min per 100 games on 8 cores,
so 5,000 games ≈ 15 h — run it overnight in 500-game chunks (a crash then
costs at most one chunk; failed individual games are skipped and logged, not
fatal). `caffeinate` stops macOS from sleeping mid-run. `--z-weight 0.75`
leans the value target on real outcomes instead of the static-evaluator
rootQ anchor — the right trade once games are plentiful. Training splits
validation by game (not sample) automatically, and gate seeds start at
100,000 so evaluation boards never overlap training boards.

```sh
# 1. 10 x 500 games, disjoint seed ranges (~90 min per chunk) — resumable:
#    completed chunks are skipped, so rerun after any interruption.
nohup bash scripts/big_run.sh > data/big_run.log 2>&1 &
# 2. train the GNN on all chunks (fewer epochs: ~700k samples need few passes)
uv run python -m net.train --data data/big_*.npz \
    --arch gnn --d 128 --rounds 4 --epochs 8 --out checkpoints/big1.pt
# 3. the gate (ensemble calibration default; add --mix 1.0 --temp 1.0 --vblend 0.0 for pure)
uv run python -m examples.net_eval --net checkpoints/big1.pt --opponent mcts --games 100
# 4. if it clears 50%+: next generation's data with the net in the loop
uv run python -m net.selfplay --games 500 --net checkpoints/big1.pt \
    --z-weight 0.75 --seed-offset 5000 --out data/big2_0.npz
```

## Play mode

The trainer app now has a second mode: **play a live 1v1 against the
champion engine** (`trainer/play.py`; toggle in the top-left of the UI).
Sessions hold a real `GameState`; you see the observation view (the bot's
hand stays server-side) while the bot runs the full gauntlet — net-guided
MCTS with exact card-tracking of *your* hand, ~1s per move at the standard
play config (`--bot`, `--bot-sims` on `trainer.server`). The service
auto-plays forced steps (rolls) and the bot's turns, streaming an event
log; discards on 7s get a pick-your-cards UI with a confirm (the one
action the codec can't express). Actions apply instantly on click,
colonist-style — take as many as you want, then ⏭ End turn; only the
trainer stages moves behind a Submit (a puzzle answer is one scored
commitment). Same board renderer and action composer as the trainer.

## The puzzle pipeline (Stage 6 / M6)

```sh
uv run python -m puzzles.pipeline --games 24 --out data/puzzles_v0.jsonl
```

Self-play games (Stage B engine) → candidate positions (every setup draft
pick, every robber decision, sampled midgame decisions with a real fan;
discards excluded) → deep labeling (default 960 sims × 8 determinizations
× 3 independent seeds) → admitted puzzles as JSONL. Admission requires a
**stable** best move across all label seeds, a **clarity gap**
Q(best) − Q(2nd) ≥ 0.04, and that the **choice matters** (not hopeless, not
won-regardless — a position whose best move wins outright while others
don't is the Catan "mate in one" and is admitted). Q-values are
visit-weighted across runs; search-starved moves (PUCT rightly ignores bad
siblings of a crushing move) get a one-step static-eval fallback in the
blunder band.

Every legal move ships with pre-computed **points** from the regret table
(Δ = Q(best) − Q(move); 100 / 75 / 40 / 10 / 0 / −25 with a 0.01 tie
epsilon), so the trainer UI needs no engine:

```python
from puzzles import load_puzzles, score_move
puzzle = load_puzzles("data/puzzles_v0.jsonl")[0]
score_move(puzzle, submitted_codec_id)   # -> points
```

Puzzle files store the perfect state (server-side; clients get
`GameState.from_dict(p.state).observation(p.actor)`), a difficulty proxy
from the gap (real difficulty comes from puzzle-Elo in M7), and a one-line
template explanation (full explanations are M8). Mining seeds live at
200,000+ — disjoint from net-training (0+) and gate (100,000+) ranges.

## The trainer app (Stage 7 / M7)

```sh
uv run python -m trainer.server --puzzles data/puzzles_v1.jsonl
# open http://localhost:8321
```

The chess.com-style trainer, end to end: an SVG board (server-computed
layout, click-to-move on highlighted vertices/edges/hexes, side buttons for
trades/dev plays), observation-level positions only (opponent hand, deck
order, and all Q/points/ranking withheld until submission — presentation
moves ship in canonical codec order because the stored ranking would
otherwise leak the answer as "first option"), regret-table scoring on
submit with the full ranked table + explanation, and **puzzle-Elo**: user
and puzzles share a rating pool (K=32/16, first attempt rated), puzzles
start from the gap-based difficulty proxy and converge to measured
difficulty. Selection prefers unseen puzzles near the user's rating with
the PLAN's 40% placement mixture. State persists in
`data/trainer_state.json`. Zero engine work at request time.

Content: `data/puzzles_v1.jsonl` — 129 puzzles (96 midgame / 15 placement /
18 robber) from 150 mining games. Regenerate or extend with
`python -m puzzles.pipeline`; relabel with the net engine post-gen-2.
Before promoting any new labeling engine, audit label churn with
`uv run python scripts/relabel_check.py data/puzzles_v1.jsonl` (baseline:
10/10 stable under the current engine; changed best-moves must be reviewed,
never silently accepted).

## Next (per PLAN.md)

Gen-2 flywheel training + gate (net-guided self-play data generating now);
relabel puzzles with the gated engine; M8 explanations.
