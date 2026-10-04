# GEN9_RUNBOOK — the next flywheel turn, exact commands

Recipe rationale (measured, see ROADMAP_V2 §A): target quality is the one
live lever — gen-7 (sims 256→400) was the last firm promotion; gen-8
(repeat at 400) was not (218/400). Gen-9 turns the same dial again:
**sims 800**, 5,000 games, rolling data window. Bot in the loop is
**gen7.pt** (the champion — gen-8 was banked but never promoted).

## 0. Pre-flight (5 minutes)

```sh
cd catan-trainer
uv run pytest -q                         # must be green
ls checkpoints/gen7.pt data/gen6_0.npz data/gen7_0.npz data/gen8_0.npz
pmset -g batt | head -1                  # macOS: MUST say AC Power
```

## 1. Rate probe — size the run before committing (15 minutes)

Self-play cost scales ~linearly with sims and ~inversely with cores.
Old-machine anchor: sims 400 → ~35 s/game wall on 8 cores; sims 800 ≈
~70 s/game there. Measure the new machine:

```sh
uv run python -m net.selfplay --games 24 --sims 800 --dets 3 \
    --net checkpoints/gen7.pt --z-weight 0.75 \
    --seed-offset 69000 --out /tmp/probe.npz
# note the wall time; per-game rate = wall / 24
```

| measured s/game | 5,000 games take | do |
|---|---|---|
| ≤ 25 | ≤ 35 h | full plan below |
| 25–60 | 35–83 h | drop to 6 chunks (3,000 games) or run the 10 chunks across several days — chunks are resumable |
| > 60 | slower than the old laptop | investigate cores/torch first (`sysctl -n hw.ncpu`, `torch.get_num_threads()`) |

Probe seeds 69000–69023 are burned either way; the real run starts at
70000 (ledger: data 0–69023 used, gates 100000–269999 used, mining
200000–213359 used).

## 2. The two scripts (write verbatim)

`scripts/gen9_run.sh`:
```sh
#!/bin/bash
# Gen-9: deep-target turn — 10 x 500 games at sims 800 with gen-7.
# Resumable: completed chunks are skipped on rerun.
set -u
cd "$(dirname "$0")/.."
BASE=70000
for i in 0 1 2 3 4 5 6 7 8 9; do
  out="data/gen9_$i.npz"
  if [ -s "$out" ]; then echo "[gen9 chunk $i] exists, skipping"; continue; fi
  echo "[gen9 chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*500))-$((BASE + i*500 + 499)))"
  caffeinate -is uv run python -m net.selfplay --games 500 --sims 800 --dets 3 \
      --net checkpoints/gen7.pt --z-weight 0.75 \
      --seed-offset $((BASE + i*500)) --out "$out" \
      || { echo "[gen9 chunk $i] FAILED"; exit 1; }
  echo "[gen9 chunk $i] done at $(date '+%H:%M:%S')"
done
echo "GEN9 ALL CHUNKS DONE at $(date)"
```
(Non-mac: drop `caffeinate -is`.)

`scripts/gen9_after.sh`:
```sh
#!/bin/bash
# Waits for gen-9 self-play, trains on the rolling window (gen6..gen9;
# gen5 rolls out — old-generation data goes inert, measured), gates.
set -u
cd "$(dirname "$0")/.."
echo "waiting for gen-9 self-play ($(date))..."
until grep -q "GEN9 ALL CHUNKS DONE" data/gen9_run.log 2>/dev/null; do sleep 300; done
echo "training ($(date))"
caffeinate -is uv run python -m net.train \
    --data data/gen6_0.npz data/gen6_1.npz data/gen6_2.npz data/gen6_3.npz \
           data/gen6_4.npz data/gen6_5.npz data/gen6_6.npz data/gen6_7.npz \
           data/gen6_8.npz data/gen6_9.npz \
           data/gen7_0.npz data/gen7_1.npz data/gen7_2.npz \
           data/gen7_3.npz data/gen7_4.npz data/gen7_5.npz \
           data/gen8_0.npz data/gen8_1.npz data/gen8_2.npz \
           data/gen8_3.npz data/gen8_4.npz data/gen8_5.npz \
           data/gen9_0.npz data/gen9_1.npz data/gen9_2.npz data/gen9_3.npz \
           data/gen9_4.npz data/gen9_5.npz data/gen9_6.npz data/gen9_7.npz \
           data/gen9_8.npz data/gen9_9.npz \
    --arch gnn --d 128 --rounds 4 --epochs 10 --board-blind-value \
    --out checkpoints/gen9.pt || { echo "TRAINING FAILED"; exit 1; }
{
  echo "=== gen-9 gates ($(date)) ==="
  echo "--- gate 1: gen9 (pure) vs raw Stage B engine, 100 games ---"
  caffeinate -is uv run python -m examples.net_eval \
      --net checkpoints/gen9.pt --opponent mcts --games 100 \
      --mix 1.0 --temp 1.0 --vblend 0.0 --seed-offset 270000 | tail -4
  echo "--- gate 2: gen9 vs incumbent gen7, 200 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen9.pt --net-b checkpoints/gen7.pt \
      --games 200 --seed-offset 280000 | tail -3
  echo "=== done ($(date)) ==="
} | tee data/gen9_report.txt
echo "GEN9 PIPELINE DONE at $(date)"
```

## 3. Launch (both at once; the after-script waits on the sentinel)

```sh
chmod +x scripts/gen9_run.sh scripts/gen9_after.sh
nohup bash scripts/gen9_run.sh   > data/gen9_run.log   2>&1 &
nohup bash scripts/gen9_after.sh > data/gen9_after.log 2>&1 &
tail -f data/gen9_run.log        # or check periodically
```
Health checks: first game line within ~3 min of a chunk start; epoch
lines every ~20–40 min during training (val value BCE should sit FLAT in
a ~0.37–0.43 band — a climb across epochs would mean the board-blind
regression, which would be a bug). After any crash/sleep/reboot: rerun
both nohup lines — everything resumes.

## 4. Reading the gates (decision table, anchored to measured history)

Gate 2 (200 games vs gen7) decides; gate 1 is context (history: 60 → 68
→ 63 → 71 → 75 → 72).

| gen9 wins /200 | verdict | action |
|---|---|---|
| ≥ 114 (57.0%) | CI-LB > 50% — **firm promotion** (gen-7's own margin) | promote |
| 107–113 | won, unresolved | extend: rerun gate 2 with `--seed-offset 290000`; promote iff combined ≥ 220/400 (gen-8 hit 218 and failed) |
| ≤ 106 | no | do not promote; the sims dial is exhausted — see §6 |

## 5. On promotion

1. Update README (Stage-5 section) + PLAN.md M5 row: gates, CI, champion
   = `checkpoints/gen9.pt`. Follow the gen-7 entries as the format.
2. Point play mode at it: `--bot checkpoints/gen9.pt` default in
   `trainer/server.py`.
3. **Labeling-engine trigger check** (ROADMAP §B3): labeling still runs
   gen-6. A firm gen-9 promotion puts the champion TWO firm promotions
   past the labeling engine → run
   `uv run python scripts/relabel_check.py data/puzzles_v5.jsonl --sample 30 --net checkpoints/gen9.pt`,
   hand-review every BEST-MOVE-CHANGED, and if sane, relabel the live set
   (`--net checkpoints/gen9.pt` on a fresh pipeline run or a relabel pass).
4. Commit: checkpoints/gen9.pt, data/gen9_*.npz, report, doc updates.

## 6. If gen-9 does NOT promote

That's two consecutive non-promotions on the deep-target recipe — the
ceiling of (this net size × this data volume × sims dial) is reached.
Stop turning the crank; the next strength gains need a regime change
(ROADMAP §A2 engine port to make 10× data cheap, §A3 true GPU batching,
or §A4 KataGo-style target tricks). Meanwhile the champion is already
25–75 points better than everything below it — product work (accounts,
review, explanations) converts that into user value with zero compute.

## Budget summary
Self-play ~35 h at the ≤25 s/game tier (background, resumable) +
training ~4–6 h (2.4 M samples) + gates ~3 h. All background; the
machine stays usable.
