#!/bin/bash
# Gen-9, scaled to this machine's measured throughput.
#
# GEN9_RUNBOOK.md's plan (5,000 games @ sims=800) assumed the anchor
# machine's ~70s/game rate. Measured on this machine (i7-11800H, 8c/16t,
# no CUDA self-play — GPU is CPU-dispatch-bound for small batches, see
# ROADMAP_V2 A3): sims=100/dets=2 averaged ~150-280s/game even with the
# default worker count (14 beat 8 workers on throughput: 178 vs 123
# games/hr in an A/B test). Extrapolated to sims=800/dets=3, that's
# ~30-40 min/game -> a full 5,000-game turn would take ~2 weeks, not
# ~35h. Scaled down 10x (500 games instead of 5,000) to fit ~34h of
# background compute while preserving sims=800 (the actual lever this
# turn is testing per ROADMAP_V2 A1 - target quality was the one
# experiment that worked; more games at the OLD sims=400 would just
# repeat gen-7/8, not test anything new).
#
# Resumable: completed chunks are skipped on rerun.
set -u
cd "$(dirname "$0")/.."
BASE=70000
for i in 0 1 2 3 4; do
  out="data/gen9_$i.npz"
  if [ -s "$out" ]; then echo "[gen9 chunk $i] exists, skipping"; continue; fi
  echo "[gen9 chunk $i] starting at $(date '+%H:%M:%S') (seeds $((BASE + i*100))-$((BASE + i*100 + 99)))"
  uv run python -m net.selfplay --games 100 --sims 800 --dets 3 \
      --net checkpoints/gen7.pt --z-weight 0.75 \
      --seed-offset $((BASE + i*100)) --out "$out" \
      || { echo "[gen9 chunk $i] FAILED"; exit 1; }
  echo "[gen9 chunk $i] done at $(date '+%H:%M:%S')"
done
echo "GEN9 SCALED RUN DONE at $(date)"
