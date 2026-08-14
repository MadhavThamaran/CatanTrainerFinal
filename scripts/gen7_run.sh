#!/bin/bash
# M5 gen-7: target-quality turn. The post-gen-6 experiments showed stale
# data contributes nothing (ablation gate 49/100), capacity buys nothing
# (d=192 gate 49/100), and search params are already right — the remaining
# lever is DEEPER self-play targets. 6 x 500 games at --sims 400 (vs 256)
# with the gen-6 champion; resumable. Seeds: base 50000 (disjoint from all
# prior data, gates 100000+, mining 200000+).
# Train on gen2_* + gen5_* + gen6_* + gen7_* (big_* retired per ablation).
set -u
cd "$(dirname "$0")/.."

BASE=50000
for i in 0 1 2 3 4 5; do
  out="data/gen7_$i.npz"
  if [ -s "$out" ]; then
    echo "[gen7 chunk $i] $out exists, skipping"
    continue
  fi
  echo "[gen7 chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*500))-$((BASE + i*500 + 499)))"
  caffeinate -is uv run python -m net.selfplay --games 500 --sims 400 --dets 3 \
      --net checkpoints/gen6.pt --z-weight 0.75 \
      --seed-offset $((BASE + i*500)) --out "$out" \
      || { echo "[gen7 chunk $i] FAILED at $(date '+%H:%M:%S')"; exit 1; }
  echo "[gen7 chunk $i] done at $(date '+%H:%M:%S')"
done
echo "GEN7 ALL CHUNKS DONE at $(date)"
