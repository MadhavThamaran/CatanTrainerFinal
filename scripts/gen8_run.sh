#!/bin/bash
# M5 gen-8: repeat of the validated gen-7 recipe (deep targets, rolling
# data window). 6 x 500 games at --sims 400 with the gen-7 champion;
# resumable. Seeds: base 60000 (disjoint from all prior data, gates
# 100000-259999, mining 200000+).
# Train on gen5_* + gen6_* + gen7_* + gen8_* (gen2 rolls out of the window).
set -u
cd "$(dirname "$0")/.."

BASE=60000
for i in 0 1 2 3 4 5; do
  out="data/gen8_$i.npz"
  if [ -s "$out" ]; then
    echo "[gen8 chunk $i] $out exists, skipping"
    continue
  fi
  echo "[gen8 chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*500))-$((BASE + i*500 + 499)))"
  caffeinate -is uv run python -m net.selfplay --games 500 --sims 400 --dets 3 \
      --net checkpoints/gen7.pt --z-weight 0.75 \
      --seed-offset $((BASE + i*500)) --out "$out" \
      || { echo "[gen8 chunk $i] FAILED at $(date '+%H:%M:%S')"; exit 1; }
  echo "[gen8 chunk $i] done at $(date '+%H:%M:%S')"
done
echo "GEN8 ALL CHUNKS DONE at $(date)"
