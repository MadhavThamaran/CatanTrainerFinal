#!/bin/bash
# Flywheel turn 7, automated: waits for gen-7 self-play, trains on the
# fresh-data window (gen2_* + gen5_* + gen6_* + gen7_*; big_* retired —
# ablation showed zero strength cost), gates vs the raw engine (100) and
# vs the incumbent gen6 with a 200-GAME head-to-head (gen-6's 54/100 was
# unresolvable at 100 games; 200 halves the CI width).
# Results land in data/gen7_report.txt. Safe to re-run after a reboot.
set -u
cd "$(dirname "$0")/.."

echo "waiting for gen-7 self-play to complete ($(date))..."
until grep -q "GEN7 ALL CHUNKS DONE" data/gen7_run.log 2>/dev/null; do
  sleep 300
done
echo "gen-7 self-play complete; training ($(date))"

caffeinate -is uv run python -m net.train \
    --data data/gen2_0.npz data/gen2_1.npz data/gen2_2.npz \
           data/gen5_0.npz data/gen5_1.npz data/gen5_2.npz \
           data/gen5_3.npz data/gen5_4.npz data/gen5_5.npz \
           data/gen6_0.npz data/gen6_1.npz data/gen6_2.npz \
           data/gen6_3.npz data/gen6_4.npz data/gen6_5.npz \
           data/gen6_6.npz data/gen6_7.npz data/gen6_8.npz data/gen6_9.npz \
           data/gen7_0.npz data/gen7_1.npz data/gen7_2.npz \
           data/gen7_3.npz data/gen7_4.npz data/gen7_5.npz \
    --arch gnn --d 128 --rounds 4 --epochs 10 \
    --board-blind-value \
    --out checkpoints/gen7.pt \
    || { echo "TRAINING FAILED"; exit 1; }

{
  echo "=== gen-7 flywheel gates ($(date)) ==="
  echo "--- gate 1: gen7 (pure) vs raw Stage B engine, 100 games ---"
  caffeinate -is uv run python -m examples.net_eval \
      --net checkpoints/gen7.pt --opponent mcts --games 100 \
      --mix 1.0 --temp 1.0 --vblend 0.0 --seed-offset 220000 | tail -4
  echo "--- gate 2: gen7 vs incumbent gen6, 200 games ---"
  caffeinate -is uv run python -m examples.net_vs_net \
      --net-a checkpoints/gen7.pt --net-b checkpoints/gen6.pt \
      --games 200 --seed-offset 230000 | tail -3
  echo "=== done ($(date)) ==="
} | tee data/gen7_report.txt

echo "GEN7 PIPELINE DONE at $(date)"
