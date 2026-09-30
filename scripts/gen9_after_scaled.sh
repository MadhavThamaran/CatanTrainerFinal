#!/bin/bash
# Waits for the SCALED gen-9 self-play run (scripts/gen9_run_scaled.sh --
# 5 x 100 games at sims=800, seeds 70000-70499, see GEN9_RUNBOOK.md +
# CLAUDE.md for why this machine's throughput needed a 10x scale-down),
# trains on the rolling window (gen6..gen9; gen9 is 5 chunks here, not
# the original plan's 10), then runs the two gates.
set -u
cd "$(dirname "$0")/.."
echo "waiting for gen-9 self-play ($(date))..."
until grep -q "GEN9 SCALED RUN DONE" data/gen9_run.log 2>/dev/null; do sleep 300; done
echo "training ($(date))"
uv run python -m net.train \
    --data data/gen6_0.npz data/gen6_1.npz data/gen6_2.npz data/gen6_3.npz \
           data/gen6_4.npz data/gen6_5.npz data/gen6_6.npz data/gen6_7.npz \
           data/gen6_8.npz data/gen6_9.npz \
           data/gen7_0.npz data/gen7_1.npz data/gen7_2.npz \
           data/gen7_3.npz data/gen7_4.npz data/gen7_5.npz \
           data/gen8_0.npz data/gen8_1.npz data/gen8_2.npz \
           data/gen8_3.npz data/gen8_4.npz data/gen8_5.npz \
           data/gen9_0.npz data/gen9_1.npz data/gen9_2.npz \
           data/gen9_3.npz data/gen9_4.npz \
    --arch gnn --d 128 --rounds 4 --epochs 10 --board-blind-value \
    --out checkpoints/gen9.pt || { echo "TRAINING FAILED"; exit 1; }
{
  echo "=== gen-9 gates ($(date)) ==="
  echo "--- gate 1: gen9 (pure) vs raw Stage B engine, 100 games (context only) ---"
  uv run python -m examples.net_eval \
      --net checkpoints/gen9.pt --opponent mcts --games 100 \
      --mix 1.0 --temp 1.0 --vblend 0.0 --seed-offset 270000 | tail -4
  echo "--- gate 2: gen9 vs incumbent gen7, 200 games (decides) ---"
  uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen9.pt --net-b checkpoints/gen7.pt \
      --games 200 --seed-offset 280000 | tail -3
  echo "=== done ($(date)) ==="
} | tee data/gen9_report.txt
echo "GEN9 PIPELINE DONE at $(date)"
