# CLAUDE.md — working in catan-trainer

A tactics trainer + engine for 1v1 Colonist-variant Catan (15 VP, no
player trades, friendly robber, balanced dice). Think "chess.com for
Catan": an AlphaZero-style engine generates puzzles and plays as a bot.

## Commands

```sh
uv run pytest -q                      # full suite (~30s, 117 tests) — run before/after changes
uv run pytest -m slow                 # strength tests (minutes)
uv run python -m trainer.server       # the web app on :8321 (trainer + play mode)
uv run python -m puzzles.pipeline --games N --net checkpoints/gen7.pt --out X.jsonl
uv run python -m net.selfplay --games N --sims S --net CKPT --seed-offset F --out X.npz
```
Requires `uv` (provisions Python 3.12). Engine is stdlib-only; net/
needs torch; trainer serving needs neither torch nor search.

## Architecture in one breath

`engine/` (exact rules, deterministic from (seed, action log)) →
`agents/` (heuristic baselines) → `search/` (determinized MCTS; the
product primitive is `MCTSEngine.evaluate(state) -> ranked per-move
Q-values`) → `net/` (GNN policy/value, AlphaZero self-play flywheel) →
`puzzles/` (mine candidates from self-play, deep-label, admit) →
`trainer/` (stdlib HTTP app: puzzle trainer + play-vs-bot).

## Current state (2026-08-14)

- **Champion: `checkpoints/gen7.pt`** (75/100 vs raw engine; gen-8 was
  NOT promoted — 218/400, CI-LB < 50%).
- **Puzzles: `data/puzzles_v5.jsonl`, 3,413 net-labeled** (trainer
  default). Labeling engine is gen-6 by policy (see ROADMAP B3).
- Promotion standard: 200-game head-to-head, CI lower bound > 50%.
- Play mode records every game to `data/games/` (replayable logs).
- `docs/EXECUTION_INDEX.md` orders all forward work; each feature has a
  full spec in `docs/*_SPEC.md`. Measured history: README Stage-5
  section + `data/*_report.txt`.

## Rules that exist because they were violated once

- **Seed ledger** — every random-seeded run must use a fresh disjoint
  range. Used: data 0–69023, 70000–70499 (gen-9, scaled down — see
  `scripts/gen9_run_scaled.sh`), gates 100000–289999 (gen-9 reserves
  270000/280000), mining 200000–213359, play sessions 300000–399999,
  lab reserves 400000+. Update this line when you consume a range.
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

## Conventions

- Engine/search stay stdlib-only; torch imports stay lazy (inside
  functions) anywhere the trainer imports.
- Every measured claim (win rates, speedups) goes in README with its CI
  and seeds; experiment reports live in `data/*_report.txt`.
- Explanatory strings shown to users derive ONLY from computed facts —
  no vibes (see EXPLAIN_SPEC honesty rules; same contract in lab,
  dashboard, coach specs).
- Tests are the porting/refactor contract: 117 passing, spec-mapped
  files per feature (`tests/test_<feature>.py`).
