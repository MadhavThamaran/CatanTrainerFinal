#!/bin/bash
# Flywheel step 3, automated: waits for gen-2 self-play to finish, then
#   1. trains gen-3 on ALL data (gen-1 big_* + gen-2 gen2_*) with the new
#      value-head dropout,
#   2. gates it vs the raw Stage B engine (pure net, fresh seeds),
#   3. gates it vs the incumbent big1.pt (net-vs-net, AZ promotion rule).
# Results land in data/gen3_report.txt.
#
# Safe to re-run after a reboot: the wait loop just resumes; training and
# gates overwrite their outputs idempotently. If gen-2 was interrupted,
# rerun scripts/gen2_run.sh first (it resumes from completed chunks).
set -u
cd "$(dirname "$0")/.."

echo "waiting for gen-2 to complete ($(date))..."
until grep -q "GEN2 ALL CHUNKS DONE" data/gen2_run.log 2>/dev/null; do
  sleep 300
done
echo "gen-2 complete; training gen-3 ($(date))"

caffeinate -is uv run python -m net.train \
    --data data/big_0.npz data/big_1.npz data/big_2.npz data/big_3.npz \
           data/big_4.npz data/big_5.npz data/big_6.npz data/big_7.npz \
           data/big_8.npz data/big_9.npz \
           data/gen2_0.npz data/gen2_1.npz data/gen2_2.npz \
    --arch gnn --d 128 --rounds 4 --epochs 8 \
    --out checkpoints/gen3fly.pt \
    || { echo "TRAINING FAILED"; exit 1; }

{
  echo "=== gen-3 flywheel gates ($(date)) ==="
  echo "--- gate 1: gen3fly (pure) vs raw Stage B engine, 100 games ---"
  caffeinate -is uv run python -m examples.net_eval \
      --net checkpoints/gen3fly.pt --opponent mcts --games 100 \
      --mix 1.0 --temp 1.0 --vblend 0.0 --seed-offset 110000 | tail -4
  echo "--- gate 2: gen3fly vs incumbent big1, 60 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen3fly.pt --net-b checkpoints/big1.pt \
      --games 60 --seed-offset 120000 | tail -3
  echo "=== done ($(date)) ==="
} | tee data/gen3_report.txt

echo "GEN3 PIPELINE DONE at $(date)"
