#!/bin/bash
# Flywheel turn 8, automated: waits for gen-8 self-play, trains on the
# rolling window (gen5_* + gen6_* + gen7_* + gen8_*; gen2 rolled out),
# gates vs the raw engine (100) and vs the incumbent gen7 (200 games —
# the promotion standard set at gen-7).
# Results land in data/gen8_report.txt. Safe to re-run after a reboot.
set -u
cd "$(dirname "$0")/.."

echo "waiting for gen-8 self-play to complete ($(date))..."
until grep -q "GEN8 ALL CHUNKS DONE" data/gen8_run.log 2>/dev/null; do
  sleep 300
done
echo "gen-8 self-play complete; training ($(date))"

caffeinate -is uv run python -m net.train \
    --data data/gen5_0.npz data/gen5_1.npz data/gen5_2.npz \
           data/gen5_3.npz data/gen5_4.npz data/gen5_5.npz \
           data/gen6_0.npz data/gen6_1.npz data/gen6_2.npz \
           data/gen6_3.npz data/gen6_4.npz data/gen6_5.npz \
           data/gen6_6.npz data/gen6_7.npz data/gen6_8.npz data/gen6_9.npz \
           data/gen7_0.npz data/gen7_1.npz data/gen7_2.npz \
           data/gen7_3.npz data/gen7_4.npz data/gen7_5.npz \
           data/gen8_0.npz data/gen8_1.npz data/gen8_2.npz \
           data/gen8_3.npz data/gen8_4.npz data/gen8_5.npz \
    --arch gnn --d 128 --rounds 4 --epochs 10 \
    --board-blind-value \
    --out checkpoints/gen8.pt \
    || { echo "TRAINING FAILED"; exit 1; }

{
  echo "=== gen-8 flywheel gates ($(date)) ==="
  echo "--- gate 1: gen8 (pure) vs raw Stage B engine, 100 games ---"
  caffeinate -is uv run python -m examples.net_eval \
      --net checkpoints/gen8.pt --opponent mcts --games 100 \
      --mix 1.0 --temp 1.0 --vblend 0.0 --seed-offset 240000 | tail -4
  echo "--- gate 2: gen8 vs incumbent gen7, 200 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen8.pt --net-b checkpoints/gen7.pt \
      --games 200 --seed-offset 250000 | tail -3
  echo "=== done ($(date)) ==="
} | tee data/gen8_report.txt

echo "GEN8 PIPELINE DONE at $(date)"
