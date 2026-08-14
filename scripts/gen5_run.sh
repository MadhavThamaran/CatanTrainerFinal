#!/bin/bash
# M5 gen-5: the first BIG flywheel turn (PLAN "bigger turns" lever).
# 6 x 500 net-guided games with the gen-4 board-blind champion in the loop,
# at the post-lockstep search speed (~2x gen-2's rate); resumable like
# big_run.sh. Seeds: base 30000 (disjoint from big 10000-14999, gen2
# 20000-21499, gates 100000+, mining 200000+).
# Train on big_*.npz + gen2_*.npz + gen5_*.npz together.
set -u
cd "$(dirname "$0")/.."

BASE=30000
for i in 0 1 2 3 4 5; do
  out="data/gen5_$i.npz"
  if [ -s "$out" ]; then
    echo "[gen5 chunk $i] $out exists, skipping"
    continue
  fi
  echo "[gen5 chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*500))-$((BASE + i*500 + 499)))"
  caffeinate -is uv run python -m net.selfplay --games 500 --sims 256 --dets 3 \
      --net checkpoints/gen4_blind.pt --z-weight 0.75 \
      --seed-offset $((BASE + i*500)) --out "$out" \
      || { echo "[gen5 chunk $i] FAILED at $(date '+%H:%M:%S')"; exit 1; }
  echo "[gen5 chunk $i] done at $(date '+%H:%M:%S')"
done
echo "GEN5 ALL CHUNKS DONE at $(date)"
