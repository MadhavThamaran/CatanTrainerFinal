#!/bin/bash
# M5 gen-6: second big flywheel turn — 10 x 500 net-guided games with the
# gen-5 champion in the loop; resumable like gen5_run.sh. Seeds: base 40000
# (disjoint from big 10000-14999, gen2 20000-21499, gen5 30000-32999,
# gates 100000+, mining 200000+).
# Train on big_* + gen2_* + gen5_* + gen6_* together.
set -u
cd "$(dirname "$0")/.."

BASE=40000
for i in 0 1 2 3 4 5 6 7 8 9; do
  out="data/gen6_$i.npz"
  if [ -s "$out" ]; then
    echo "[gen6 chunk $i] $out exists, skipping"
    continue
  fi
  echo "[gen6 chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*500))-$((BASE + i*500 + 499)))"
  caffeinate -is uv run python -m net.selfplay --games 500 --sims 256 --dets 3 \
      --net checkpoints/gen5.pt --z-weight 0.75 \
      --seed-offset $((BASE + i*500)) --out "$out" \
      || { echo "[gen6 chunk $i] FAILED at $(date '+%H:%M:%S')"; exit 1; }
  echo "[gen6 chunk $i] done at $(date '+%H:%M:%S')"
done
echo "GEN6 ALL CHUNKS DONE at $(date)"
