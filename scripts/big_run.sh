#!/bin/bash
# M5 large self-play run: 10 x 500 games (PLAN Stage 5, README recipe).
#
# - Resumable: a chunk whose .npz already exists is skipped, so rerunning
#   this script after any interruption continues where it left off (a chunk
#   only writes its file on completion; a partial chunk restarts).
# - caffeinate keeps macOS awake for the duration.
# - Seeds: base 10000 (disjoint from earlier data 0-599, gates 100000+,
#   puzzle mining 200000+). Train on data/big_*.npz ONLY — do not mix the
#   old gen1_* files (overlapping seed ranges and z-weights).
set -u
cd "$(dirname "$0")/.."

BASE=10000
for i in 0 1 2 3 4 5 6 7 8 9; do
  out="data/big_$i.npz"
  if [ -s "$out" ]; then
    echo "[chunk $i] $out exists, skipping"
    continue
  fi
  echo "[chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*500))-$((BASE + i*500 + 499)))"
  caffeinate -is uv run python -m net.selfplay --games 500 --sims 400 \
      --z-weight 0.75 --seed-offset $((BASE + i*500)) --out "$out" \
      || { echo "[chunk $i] FAILED at $(date '+%H:%M:%S')"; exit 1; }
  echo "[chunk $i] done at $(date '+%H:%M:%S')"
done
echo "ALL CHUNKS DONE at $(date)"
