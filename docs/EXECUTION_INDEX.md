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
- [x] **SRS_SPEC.md** — spaced repetition. DONE 2026-09-29: Leitner boxes
      (`trainer/srs.py`), `/api/srs/summary` + `/api/srs/next`, `srs:true`
      on `/api/submit` (unrated). Header chip `🔁 Review (N)`.

## Phase 2 — the improvement loop
- [x] **REVIEW_SPEC.md** — post-game review. DONE 2026-09-29:
      `trainer/review.py` + `/api/review/start` + `/api/review/poll` +
      the review UI (accuracy, verdict chips, win-prob graph, click-a-
      move board replay). (Game recording + replay-integrity test: DONE,
      shipped earlier.)
- [x] **COACH_SPEC.md** — pre-move interjection coaching. DONE 2026-09-29:
      `PlaySession.submit()` gates on a coach `MCTSEngine` (same checkpoint,
      human's info set); flagged moves bounce with state UNCHANGED, "play
      it anyway" applies with a verdict badge. `coach_set` toggle on
      `/api/play/act`, `?coach=1` on `/api/play/new`. `🎓 Coach` badge +
      interjection card in the UI.
- [x] **DASHBOARD_SPEC.md** — weakness dashboard. DONE 2026-09-29:
      `trainer/dashboard.py` (Form/Skills/Leaks/Cost cards),
      per-skill Elo in `Ratings`, `/api/dashboard`, phase-filtered
      `/api/next?phase=`, "📈 Progress" UI. Coach-override and
      placement-lab leak types stay absent (nullable) until those ship.
- [x] **EXPLAIN_SPEC.md** — facts-based explanations. DONE 2026-09-29:
      `puzzles/explain.py` (`move_facts`/`render`/`render_miss`, no
      search), wired into puzzle labeling (`Puzzle.facts`), the puzzle
      result screen and "yours missed X" (`trainer/service.py`), and
      post-game review rows (`row.why`). `scripts/annotate_explanations.py`
      backfills the existing puzzle set.

## Phase 3 — depth
- [x] **LADDER_SPEC.md** — bot ladder. DONE 2026-09-29: `trainer/ladder.py`
      (8 rungs, `make_bot`, unlock/star/Elo math), play mode now requires
      login and takes `?rung=&rated=`, `/api/ladder` for the pre-game
      screen, per-user ladder state in `Ratings.ladder`. Client: rung-card
      picker, in-game rung/rated badge, unlock/Elo toast, Rematch.
      `scripts/ladder_calibrate.py` written but NOT YET RUN (the real
      ~8-10h measurement pass) — rungs report provisional Elo until it is.
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
