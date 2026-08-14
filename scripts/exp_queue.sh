#!/bin/bash
# Post-gen-6 experiment queue — one CPU-heavy job at a time:
#   1. big puzzle mining run: net-guided games AND net-guided deep labeling
#      (the audited labeling switch), broadened candidate types -> v3 set
#   2. stale-data ablation: gen-6 recipe WITHOUT the heuristic-era big_*
#      chunks; gate vs gen6 head-to-head
#   3. capacity probe: d=192 GNN on ALL data; gate vs gen6
#   4. search-param grid around net priors (c_puct / prior temperature)
# Each stage logs to data/; safe to re-run (stages skip if output exists).
set -u
cd "$(dirname "$0")/.."

if [ ! -s data/puzzles_v3.jsonl ]; then
  echo "=== stage 1: mining v3 puzzle set ($(date)) ==="
  caffeinate -is uv run python -m puzzles.pipeline \
      --games 240 --net checkpoints/gen6.pt \
      --seed-offset 210000 --out data/puzzles_v3.jsonl \
      || { echo "MINING FAILED"; exit 1; }
else
  echo "=== stage 1 skipped: data/puzzles_v3.jsonl exists ==="
fi

if [ ! -s checkpoints/gen6_noheur.pt ]; then
  echo "=== stage 2: stale-data ablation ($(date)) ==="
  caffeinate -is uv run python -m net.train \
      --data data/gen2_0.npz data/gen2_1.npz data/gen2_2.npz \
             data/gen5_0.npz data/gen5_1.npz data/gen5_2.npz \
             data/gen5_3.npz data/gen5_4.npz data/gen5_5.npz \
             data/gen6_0.npz data/gen6_1.npz data/gen6_2.npz \
             data/gen6_3.npz data/gen6_4.npz data/gen6_5.npz \
             data/gen6_6.npz data/gen6_7.npz data/gen6_8.npz data/gen6_9.npz \
      --arch gnn --d 128 --rounds 4 --epochs 10 --board-blind-value \
      --out checkpoints/gen6_noheur.pt \
      || { echo "ABLATION TRAINING FAILED"; exit 1; }
  echo "--- ablation gate: gen6_noheur vs gen6, 100 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen6_noheur.pt --net-b checkpoints/gen6.pt \
      --games 100 --seed-offset 190000 | tail -3
else
  echo "=== stage 2 skipped: checkpoints/gen6_noheur.pt exists ==="
fi

if [ ! -s checkpoints/gen6_d192.pt ]; then
  echo "=== stage 3: capacity probe d=192 ($(date)) ==="
  caffeinate -is uv run python -m net.train \
      --data data/big_0.npz data/big_1.npz data/big_2.npz data/big_3.npz \
             data/big_4.npz data/big_5.npz data/big_6.npz data/big_7.npz \
             data/big_8.npz data/big_9.npz \
             data/gen2_0.npz data/gen2_1.npz data/gen2_2.npz \
             data/gen5_0.npz data/gen5_1.npz data/gen5_2.npz \
             data/gen5_3.npz data/gen5_4.npz data/gen5_5.npz \
             data/gen6_0.npz data/gen6_1.npz data/gen6_2.npz \
             data/gen6_3.npz data/gen6_4.npz data/gen6_5.npz \
             data/gen6_6.npz data/gen6_7.npz data/gen6_8.npz data/gen6_9.npz \
      --arch gnn --d 192 --rounds 4 --epochs 8 --board-blind-value \
      --out checkpoints/gen6_d192.pt \
      || { echo "D192 TRAINING FAILED"; exit 1; }
  echo "--- capacity gate: gen6_d192 vs gen6, 100 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen6_d192.pt --net-b checkpoints/gen6.pt \
      --games 100 --seed-offset 195000 | tail -3
else
  echo "=== stage 3 skipped: checkpoints/gen6_d192.pt exists ==="
fi

echo "=== stage 4: search-param grid ($(date)) ==="
caffeinate -is uv run python scripts/param_grid.py \
    --net checkpoints/gen6.pt --games 60

echo "EXP QUEUE DONE at $(date)"
