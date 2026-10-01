# CLAUDE.md — working in catan-trainer

A tactics trainer + engine for 1v1 Colonist-variant Catan (15 VP, no
player trades, friendly robber, balanced dice). Think "chess.com for
Catan": an AlphaZero-style engine generates puzzles and plays as a bot.

## Commands

```sh
uv run pytest -q                      # full suite (~1-4min, 221 tests) — run before/after changes
uv run pytest -m slow                 # strength tests (minutes)
uv run python -m trainer.server       # the web app on :8321 (trainer + play mode + review)
uv run python -m puzzles.pipeline --games N --net checkpoints/gen7.pt --out X.jsonl
uv run python -m net.selfplay --games N --sims S --net CKPT --seed-offset F --out X.npz
```
Requires `uv` (provisions Python 3.12; a pinned Python may not always be
available — falls back to whatever `uv` finds). Engine is stdlib-only;
net/ needs torch (optional `play` extra, or the `dev` group for local
work); trainer serving needs numpy (real base dependency — puzzles ->
net.codec pulls it in) plus psycopg when `DATABASE_URL` is set, but no
torch/search unless play mode (`--no-play` to disable) is on.

## Architecture in one breath

`engine/` (exact rules, deterministic from (seed, action log)) →
`agents/` (heuristic baselines) → `search/` (determinized MCTS; the
product primitive is `MCTSEngine.evaluate(state) -> ranked per-move
Q-values`) → `net/` (GNN policy/value, AlphaZero self-play flywheel) →
`puzzles/` (mine candidates from self-play, deep-label, admit) →
`trainer/` (stdlib HTTP app: puzzle trainer + play-vs-bot).

## Current state (2026-09-30)

- **Champion: `checkpoints/gen7.pt`** (75/100 vs raw engine; gen-8 was
  NOT promoted — 218/400, CI-LB < 50%). Gen-9 (scaled 500-game run at
  sims=800) finished its full train+gate pipeline 2026-09-30: vs raw
  67/100, vs gen7 **99/200 = 49.5% — NOT promoted** (`data/gen9_report.txt`).
  **Two consecutive non-promotions — the sims-dial lever is exhausted.**
  Next real strength gains need a regime change (engine port, true GPU
  batching, or KataGo-style target tricks), not another same-recipe turn;
  see README Stage-5 / PLAN.md M5 for the full measured history.
- **Puzzles: `data/puzzles_v5.jsonl`, 3,413 net-labeled** (trainer
  default). Labeling engine is gen-6 by policy (see ROADMAP B3).
- Promotion standard: 200-game head-to-head, CI lower bound > 50%.
- **Hosting (HOSTING.md step 1) shipped**: accounts (`trainer/store.py`
  `Store`/`JsonStore`/`PgStore`, `trainer/auth.py` scrypt + signed
  cookies), per-user ratings, login/signup UI. Live at
  `https://catantrainerfinal.onrender.com` (Render free tier + Neon
  Postgres; `--no-play` there since play mode needs torch).
- **Post-game review (REVIEW_SPEC.md) shipped**: `trainer/review.py`
  scores every human play-mode decision at deeper search than play used,
  chess.com-style report (accuracy, verdict chips, win-prob graph,
  click-a-move board replay). `/api/review/start`, `/api/review/poll`.
- **SRS (SRS_SPEC.md) shipped**: `trainer/srs.py` — Leitner boxes on
  rated trainer misses (<75 pts), stored in the same per-user ratings
  blob. `/api/srs/summary`, `/api/srs/next`, `srs:true` on `/api/submit`
  (scored for display, never touches Elo). Header chip `🔁 Review (N)`.
- **Weakness dashboard (DASHBOARD_SPEC.md) shipped**: `trainer/dashboard.py`
  aggregates per-skill Elo (`Ratings.skill`, new), play-mode game/review
  records into Form/Skills/Leaks/Cost cards. `/api/dashboard` (30s
  cache), `/api/next?phase=` drill filter. Coach-override and
  placement-lab leak types are absent (nullable) until those specs ship.
- **Coach mode (COACH_SPEC.md) shipped**: `PlaySession.submit()` gates
  every human play-mode action through a coach `MCTSEngine` (same
  checkpoint as the bot, judged from the human's info set); a move over
  the severity threshold bounces with state UNCHANGED (no dice thrown, no
  cards stolen), "play it anyway" applies it with a `verdict`/`coached`
  event tag; every non-flagged move gets the same verdict as a free
  passive badge. `coach_set` on `/api/play/act`, `?coach=` on
  `/api/play/new`. `🎓 Coach` badge + interjection card in the UI.
- **Explanations (EXPLAIN_SPEC.md) shipped**: `puzzles/explain.py` —
  `move_facts(state, action, actor)` (exact, no-search position diff) and
  `render(facts_best, facts_alt, phase)` (template clauses that cite ONLY
  facts leaf values, `None` when nothing salient). Wired into puzzle
  labeling (`Puzzle.facts`, backfilled onto `puzzles_v5.jsonl` via
  `scripts/annotate_explanations.py`), the puzzle result screen +
  "yours missed X" (`trainer/service.py::_explain`), and post-game review
  rows (`trainer/review.py`'s `row.why`).
- **Bot ladder (LADDER_SPEC.md) shipped**: `trainer/ladder.py` — 8 rungs
  (Settler..The Engine, `make_bot` resolves HeuristicAgent/raw MCTS/net
  MCTS per rung), per-user state in `Ratings.ladder` (play-Elo K=24,
  W/L/stars/unlocks, `record_game` pure function). **Play mode now
  requires login** (it didn't before this). `/api/play/new?rung=&rated=`,
  `/api/ladder` for the pre-game rung-picker screen; game records gain
  `rung`/`rated`. Coach judges at a fixed reference net regardless of
  rung (COACH_SPEC's own logic untouched). Ratings are PROVISIONAL
  (declared guesses) until `scripts/ladder_calibrate.py`'s ~8-10h
  measurement pass is actually run — written but not yet executed.
- **Placement lab (PLACEMENT_LAB_SPEC.md) shipped**: `trainer/lab.py` —
  `LabService`/`LabSession` drill the 1v1 setup snake (A-B-B-A); each of
  the human's 4 decisions (2 settlements + 2 roads) is graded BEFORE it
  applies (single-seed `MCTSEngine` pass, `LAB_SIMS=256/LAB_DETS=4`),
  with a percentile, an EXPLAIN-rendered sentence (settlement picks), and
  board-mark rings. Attempts append to `data/lab_attempts.jsonl`
  (per-user, no Store/Ratings coupling — structurally can't touch
  puzzle-Elo). `/api/lab/new`, `/api/lab/act`, `/api/lab/stats` (bias
  deltas + threshold statements over the last 50 settlement picks,
  n>=15 gated). Client: `🧪 Placement lab` mode, grade card, drill
  summary, stats card.
- **Analysis board (ANALYSIS_SPEC.md) shipped**: `engine/chance.py` —
  `force_next_roll`/`force_next_steal`/`force_next_draw` (each a
  one-shot override that consumes zero extra entropy, so the stream
  after is bit-identical to unforced); `search/determinize.py`'s
  `Determinizer(exact=True)` skips hidden-info resampling for the
  already-perfect states analysis explores. `trainer/analysis.py`:
  content-addressed node tree (same forced line reuses the node + its
  cached eval; unforced/"random" always forks fresh), 200-node cap
  errors instead of pruning. `/api/analysis/new|eval|apply|tree`. Client:
  "Analyze" on puzzle results + review rows (a 4th APP_MODE reusing the
  board/marks/action-bar idioms), revealed-hands card, engine-lines
  card, chance picker with real odds (`DicePolicy.probabilities()`, new).
- Play mode records every game to `data/games/` (replayable logs) — the
  foundation review stands on.
- `docs/EXECUTION_INDEX.md` orders all forward work; each feature has a
  full spec in `docs/*_SPEC.md`. Measured history: README Stage-5
  section + `data/*_report.txt`.

## Rules that exist because they were violated once

- **Seed ledger** — every random-seeded run must use a fresh disjoint
  range. Used: data 0–69023, 70000–70499 (gen-9, scaled down — see
  `scripts/gen9_run_scaled.sh`), gates 100000–289999 (gen-9 reserves
  270000/280000), mining 200000–213359, play sessions 300000–399999,
  lab drills 400000–90399999 (`trainer/lab.py` samples randomly across
  this whole span — "endless fresh boards" per spec, not a one-shot run).
  Update this line when you consume a range.
- **Long runs die to battery hibernation** on macOS laptops —
  `caffeinate -is` only holds on AC. All pipeline scripts are
  chunk-resumable: rerun after any interruption; completed chunks skip.
- **`puzzles.pipeline` writes output only at the END of labeling** —
  don't start a run you can't finish; hours of labeling are lost on a
  kill.
- **Training data files store ENCODED feature vectors** — changing
  `net/encode.py` layout invalidates every `.npz`. Derive new model
  inputs INSIDE the model from the existing vector (see
  `_BlindValueFeatures` for the pattern), or accept regenerating data.
- **Elo isolation** — lesson/SRS/lab/review attempts must never touch
  the rated puzzle-Elo pool. Guard with tests when adding modes.
- **Codec can't express discards** — any surface serving discard
  decisions needs a custom path (see `trainer/play.py`).
- Engine changes must keep the (seed, action-log) determinism contract —
  `tests/test_play.py::test_game_record_replays_to_identical_outcome`
  is the tripwire.
- **`TOPOLOGY.vertex_hexes[v]` (vertex -> hexes) vs `TOPOLOGY.hex_vertices[h]`
  (hex -> vertices) are easy to swap** — `agents/heuristic.py::_robber_score`
  did exactly this (indexed `vertex_hexes[a.hex]`, a hex id, into the
  vertex-keyed table) and was silently blind to buildings on the hex it
  was scoring for the life of the project. No training/labeling impact
  (self-play and labeling always run at `net_prior_mix=1.0`, which never
  touches the heuristic prior — see PLAN.md M5), but it did make the "vs
  raw engine" context numbers in README/PLAN noisier than reported. Fixed
  2026-09-29; guarded by
  `tests/test_agents.py::test_robber_score_finds_buildings_on_the_targeted_hex`.

## Conventions

- Engine/search stay stdlib-only; torch imports stay lazy (inside
  functions) anywhere the trainer imports.
- Every measured claim (win rates, speedups) goes in README with its CI
  and seeds; experiment reports live in `data/*_report.txt`.
- Explanatory strings shown to users derive ONLY from computed facts —
  no vibes (see EXPLAIN_SPEC honesty rules; same contract in lab,
  dashboard, coach specs).
- Tests are the porting/refactor contract: 221 passing, spec-mapped
  files per feature (`tests/test_<feature>.py`).
