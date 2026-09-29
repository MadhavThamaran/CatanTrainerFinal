# EXECUTION_INDEX — all forward work, dependency-ordered

Point a coding session here. Each item links a full spec with decided
trade-offs, data models, APIs, tests, and acceptance criteria — execute,
don't re-design. Statuses as of 2026-08-14.

## Phase 0 — machine transfer (human, ~30 min)
- [ ] HANDOFF.md §1b–§2: zip/transfer (or git push), clone-equivalent on
      the new machine, `uv run pytest -q` green (117).

## Phase 1 — public product (unlocks everything user-data-driven)
- [x] **HOSTING.md** — accounts (scrypt + signed cookies), storage seam
      (JsonStore/PgStore), Render + Neon deploy of the puzzle trainer.
      DONE 2026-09-29: live at https://catantrainerfinal.onrender.com.
      DASHBOARD_SPEC §0 per-skill-Elo addition still open (not done in
      the same change — revisit when building the dashboard).
- [ ] **SRS_SPEC.md** — spaced repetition. ~0.75 day. Ship with accounts
      so queues are per-user from birth.

## Phase 2 — the improvement loop
- [x] **REVIEW_SPEC.md** — post-game review. DONE 2026-09-29:
      `trainer/review.py` + `/api/review/start` + `/api/review/poll` +
      the review UI (accuracy, verdict chips, win-prob graph, click-a-
      move board replay). (Game recording + replay-integrity test: DONE,
      shipped earlier.)
- [ ] **COACH_SPEC.md** — pre-move interjection coaching. ~1 day.
- [ ] **DASHBOARD_SPEC.md** — weakness dashboard. ~1 day after review
      exists (attempt-log enrichment: DONE, shipped).
- [ ] **EXPLAIN_SPEC.md** — facts-based explanations + annotate the
      existing 3,413 puzzles. ~1 day. Upgrades review/coach/lab output
      wherever it lands earlier.

## Phase 3 — depth
- [ ] **LADDER_SPEC.md** — bot ladder + overnight calibration run.
      ~1.5 days + one unattended night.
- [ ] **PLACEMENT_LAB_SPEC.md** — placement drills + bias analytics.
      ~1.5–2 days. Better after EXPLAIN.
- [ ] **ANALYSIS_SPEC.md** — analysis board with choosable dice. ~2
      days. Better after REVIEW (its entry point).
- [ ] **CURRICULUM_SPEC.md** — six lesson tracks. ~2.5 days (content is
      the long pole). Better after EXPLAIN.

## Engine track (parallel, background compute)
- [ ] **GEN9_RUNBOOK.md** — rate-probe first; sims-800 turn; promotion
      by decision table. Runs unattended alongside any phase.
- [ ] ROADMAP_V2 §B1 — difficulty recalibration (needs ~500 hosted
      attempts; small, unspec'd deliberately).
- [ ] **RUST_PORT_SPEC.md** — engine hot-loop port, ~1-2 weeks; do when
      self-play is the measured bottleneck again.

## Standing references
- CLAUDE.md — conventions + the rules that exist because they were
  violated once. ROADMAP_V2.md — rationale + measured history behind
  every ranking above. README — full project narrative.

Total: ~12–14 focused days to the complete product vision, engine track
running in the background throughout.
