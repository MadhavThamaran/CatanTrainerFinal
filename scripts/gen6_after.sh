#!/bin/bash
# Flywheel turn 6, automated: waits for gen-6 self-play, trains on ALL data
# (big_* + gen2_* + gen5_* + gen6_*, ~14,500 games), gates vs the raw
# engine and vs the incumbent gen5 (100 games each, fresh seeds).
# Results land in data/gen6_report.txt. Safe to re-run after a reboot.
set -u
cd "$(dirname "$0")/.."

echo "waiting for gen-6 self-play to complete ($(date))..."
until grep -q "GEN6 ALL CHUNKS DONE" data/gen6_run.log 2>/dev/null; do
  sleep 300
done
echo "gen-6 self-play complete; training ($(date))"

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
    --arch gnn --d 128 --rounds 4 --epochs 10 \
    --board-blind-value \
    --out checkpoints/gen6.pt \
    || { echo "TRAINING FAILED"; exit 1; }

{
  echo "=== gen-6 flywheel gates ($(date)) ==="
  echo "--- gate 1: gen6 (pure) vs raw Stage B engine, 100 games ---"
  caffeinate -is uv run python -m examples.net_eval \
      --net checkpoints/gen6.pt --opponent mcts --games 100 \
      --mix 1.0 --temp 1.0 --vblend 0.0 --seed-offset 170000 | tail -4
  echo "--- gate 2: gen6 vs incumbent gen5, 100 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen6.pt --net-b checkpoints/gen5.pt \
      --games 100 --seed-offset 180000 | tail -3
  echo "=== done ($(date)) ==="
} | tee data/gen6_report.txt

echo "GEN6 PIPELINE DONE at $(date)"
