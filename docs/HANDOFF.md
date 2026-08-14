# HANDOFF.md — leaving this machine, resuming on the next

This laptop goes away soon. Everything below is ordered: do the *this
machine* section before it's gone, the rest on the new machine.

## 1. Before leaving THIS machine

### 1a. v4 puzzle batch — DONE (2026-08-14)
The v4 batch landed (1,956 puzzles from 360 gen-7-guided games) and was
merged: **`data/puzzles_v5.jsonl` = 3,413 puzzles** (812 placement,
1,394 robber, 371 midgame, 337 devcard, 332 trade, 167 endgame), no id
collisions. The trainer's default is already `puzzles_v5.jsonl`; tests
green. Nothing left to do here.

### 1b. Push everything to GitHub (from this machine)
The repo is NOT under git. All checkpoints (~10 MB each) and data chunks
(~8 MB each) are under GitHub's 100 MB/file limit — no LFS needed; the
repo lands around 400 MB, which GitHub accepts.

```sh
cd ~/projects/catan-trainer
git init
printf '%s\n' __pycache__/ .venv/ '*.log' 'nohup.out' data/trainer_server.log > .gitignore
git add -A
git commit -m "catan-trainer: engine, flywheel gens 1-8, puzzle sets v1-v5, trainer + play mode"
# create the repo under your PERSONAL account (log in as it first):
gh auth login            # choose github.com, personal account
gh repo create catan-trainer --private --source=. --push
```

If `gh` is unavailable: create an empty private repo in the browser, then
`git remote add origin git@github.com:YOU/catan-trainer.git && git push -u origin main`.

What must not be lost (all included by `git add -A`):
- `checkpoints/gen7.pt` — the champion (play mode + future labeling)
- `data/puzzles_v*.jsonl` — the product content
- `data/*.npz` — 14,500 games of training data (only needed for future
  training; drop from the repo only if size becomes a problem)
- `data/trainer_state.json` — your puzzle-Elo rating
- `data/*_report.txt`, `data/relabel_audit_gen6.txt` — the evidence trail

### 1c. Sanity check
`git status` clean, `gh repo view --web` shows the files, and
`uv run pytest -q` is green (116 tests as of writing).

## 2. On the NEW machine

```sh
git clone git@github.com:YOU/catan-trainer.git && cd catan-trainer
uv run pytest -q                          # should be green
uv run python -m trainer.server           # trainer + play mode on :8321
```

Machine notes that carried real pain on the old laptop:
- Long runs die to **battery hibernation** — `caffeinate -is` only holds
  on AC power. Keep it plugged in; every pipeline script is
  chunk-resumable (rerun it, completed chunks are skipped).
- **Seed ledger** (keep ranges disjoint): training data used 0–599,
  10000–14999, 20000+, 30000+, 40000+, 50000+, 60000+ (gen8); gates used
  100000–269999; mining 200000–213359. Next free: data 70000, gates
  270000, mining 214000.
- The pipeline (`puzzles.pipeline`) writes output only at the END of
  labeling — don't start a mining run you can't finish.

## 3. What to build next (see the companion docs)
- `docs/HOSTING.md` — accounts + public deployment of the puzzle trainer
- `docs/ROADMAP_V2.md` — bot strength and product plans, with effort and
  expected-value notes per item
