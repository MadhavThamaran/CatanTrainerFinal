#!/bin/bash
# M5 gen-2: first NET-GUIDED self-play generation (the AlphaZero flywheel).
# 3 x 500 games with big1.pt in the loop; resumable like big_run.sh.
# Seeds: base 20000 (disjoint from gen-1 10000-14999, gates 100000+,
# mining 200000+). Train on big_*.npz + gen2_*.npz together.
set -u
cd "$(dirname "$0")/.."

BASE=20000
for i in 0 1 2; do
  out="data/gen2_$i.npz"
  if [ -s "$out" ]; then
    echo "[gen2 chunk $i] $out exists, skipping"
    continue
  fi
  echo "[gen2 chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*500))-$((BASE + i*500 + 499)))"
  caffeinate -is uv run python -m net.selfplay --games 500 --sims 256 --dets 3 \
      --net checkpoints/big1.pt --z-weight 0.75 \
      --seed-offset $((BASE + i*500)) --out "$out" \
      || { echo "[gen2 chunk $i] FAILED at $(date '+%H:%M:%S')"; exit 1; }
  echo "[gen2 chunk $i] done at $(date '+%H:%M:%S')"
done
echo "GEN2 ALL CHUNKS DONE at $(date)"
