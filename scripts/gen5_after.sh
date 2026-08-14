#!/bin/bash
# Flywheel turn 5, automated: waits for gen-5 self-play to finish, then
#   1. trains gen-5 on ALL data (big_* + gen2_* + gen5_*, ~9,500 games)
#      with the board-blind value path; 10 epochs — usable now that the
#      value head no longer diverges,
#   2. gates it vs the raw Stage B engine (pure net, fresh seeds),
#   3. gates it vs the incumbent gen4_blind (100 games this time: both
#      prior promotions were within noise at 60).
# Results land in data/gen5_report.txt. Safe to re-run after a reboot.
set -u
cd "$(dirname "$0")/.."

echo "waiting for gen-5 self-play to complete ($(date))..."
until grep -q "GEN5 ALL CHUNKS DONE" data/gen5_run.log 2>/dev/null; do
  sleep 300
done
echo "gen-5 self-play complete; training ($(date))"

caffeinate -is uv run python -m net.train \
    --data data/big_0.npz data/big_1.npz data/big_2.npz data/big_3.npz \
           data/big_4.npz data/big_5.npz data/big_6.npz data/big_7.npz \
           data/big_8.npz data/big_9.npz \
           data/gen2_0.npz data/gen2_1.npz data/gen2_2.npz \
           data/gen5_0.npz data/gen5_1.npz data/gen5_2.npz \
           data/gen5_3.npz data/gen5_4.npz data/gen5_5.npz \
    --arch gnn --d 128 --rounds 4 --epochs 10 \
    --board-blind-value \
    --out checkpoints/gen5.pt \
    || { echo "TRAINING FAILED"; exit 1; }

{
  echo "=== gen-5 flywheel gates ($(date)) ==="
  echo "--- gate 1: gen5 (pure) vs raw Stage B engine, 100 games ---"
  caffeinate -is uv run python -m examples.net_eval \
      --net checkpoints/gen5.pt --opponent mcts --games 100 \
      --mix 1.0 --temp 1.0 --vblend 0.0 --seed-offset 150000 | tail -4
  echo "--- gate 2: gen5 vs incumbent gen4_blind, 100 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen5.pt --net-b checkpoints/gen4_blind.pt \
      --games 100 --seed-offset 160000 | tail -3
  echo "=== done ($(date)) ==="
} | tee data/gen5_report.txt

echo "GEN5 PIPELINE DONE at $(date)"
