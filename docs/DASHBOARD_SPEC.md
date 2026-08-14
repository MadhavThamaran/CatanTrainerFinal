# DASHBOARD_SPEC — the weakness dashboard ("your coach's notebook")

The dashboard answers three questions the raw activities can't: *where am
I weak, is it costing me games, and what exactly should I drill next?*
It is aggregation + honest statements over data the other features
already produce — no engine time, no new content.

## 0. Data audit (what exists vs what must be enriched)

| source | has today | dashboard needs |
|---|---|---|
| puzzle attempts (`trainer/elo.py` history) | `{pid, points, rated}` capped at 500 | **enrich**: add `phase`, `regret`, `ts`, `puzzle_rating`; raise cap to 2000 |
| per-skill rating | one global user Elo | **add**: per-phase Elo dict updated in `record()` |
| game records (`data/games/*.json`) | full logs + winner (shipped) | read-only |
| review results (`*.review.json`, REVIEW_SPEC) | per-decision regret/verdict | read-only once built |
| coach events (COACH_SPEC) | `coached` fields in game logs | read-only once built |
| lab attempts (PLACEMENT_LAB_SPEC) | per-pick facts deltas | embed lab's own stats payload |

**Enrichment is the only prerequisite** (small `elo.py`/`service.py`
change, backward-compatible: old history entries simply lack fields and
are skipped by aggregations). Do it EARLY — every attempt logged before
it is a thin data point forever.

### Per-skill Elo (the core mechanic)
`Ratings` gains `self.skill: {phase: rating}` (same 1500 start). On each
RATED attempt, update both the global rating and the attempt's phase
rating with the same `(s - expected)` delta against the puzzle's rating.
Phases = the six puzzle tags: placement / robber / trade / devcard /
endgame / midgame. Skill ratings converge with ~15–20 attempts each; the
UI shows a confidence dot until n ≥ 15.

## 1. Dashboard content (one screen, four cards)

1. **Form** — global puzzle Elo + last-100 sparkline; games vs bot W/L;
   review accuracy trend across last 10 reviewed games (mean accuracy,
   REVIEW_SPEC §3). Nullable sections render as "play/review more".
2. **Skills** — horizontal bars: per-phase Elo relative to global, with
   attempt counts. The visual answer to "where am I weak."
3. **Leaks** — max 4 statements from the statements engine (§2), each
   with a **"Drill this →"** button (§3). Examples:
   - "Robber play is your weakest skill (1410 vs 1560 overall, n=22)."
   - "Reviews: 7 of your last 9 blunders were mid-game build choices."
   - "You override coach warnings 4 times in 5 — the warnings are right
     more often than that."
   - "Placement lab: you overvalue ore-heavy spots (lab bias feed)."
4. **Cost** — regret economics from reviews: mean win-prob leaked per
   game, split by game stage (setup / early / mid / endgame via turn
   number), trend arrow over the last 10 games. The "is it costing me
   games" number.

## 2. Statements engine (shared honesty rules)

One module (`trainer/dashboard.py`), same contract as EXPLAIN_SPEC and
the lab: **statements derive only from stored numbers, thresholds are
explicit, and minimum-n gates suppress noise**:

- skill gap: phase Elo < global − 100 with n ≥ 15
- review taxonomy: ≥ 60% of blunders share an action-type group
  (groups: robber moves / builds / trades / dev plays / discards /
  end-turn timing) with ≥ 5 blunders total
- coach override: played-anyway rate ≥ 0.6 with ≥ 10 interjections,
  AND mean regret of overridden moves confirms the warnings were right
- lab biases: passed through from the lab stats payload verbatim
- If nothing clears a gate: "No clear leaks yet — keep playing" (never
  invent a weakness).

Each statement carries machine-readable provenance
(`{"kind": "skill_gap", "phase": "robber", "n": 22, ...}`) so the UI can
render drill links and tests can assert exact triggering.

## 3. "Drill this →" (closing the loop)

The dashboard's statements deep-link into the existing modes:
- skill gap → trainer session filtered to that phase. Requires a small
  addition: `GET /api/next?phase=robber` — a pool filter in
  `Ratings.pick` (keep the 40% placement mixture only for unfiltered
  sessions).
- placement statements → the Placement lab.
- review-taxonomy statements → trainer filtered to the matching tag,
  plus a "review your recent games" link if unreviewed games exist.
This is the feature's whole point: measurement that ends in a button,
not a chart.

## 4. API + UI

- `GET /api/dashboard` → `{form: {...}, skills: [...], leaks: [...],
  cost: {...}}`, each section nullable. Pure reads: ratings state +
  `data/games/*.json` + `*.review.json` + lab stats. Cache for 30s
  (cheap anyway).
- UI: fourth mode button `📈 Progress`. Cards as above; skill bars and
  the sparkline are tiny inline SVGs (the win-prob graph in REVIEW_SPEC
  establishes the idiom). Every leak row ends in its drill button.

## 5. Tests (`tests/test_dashboard.py`)

- Per-skill Elo math: synthetic rated attempts → hand-computed phase
  ratings; global rating unchanged vs old behavior (regression).
- Back-compat: pre-enrichment history entries (no phase/regret) don't
  crash aggregations and are excluded from counts.
- Statements gating: fixtures just under / just over each threshold and
  n-gate; assert exact statement sets (via provenance, not string
  matching).
- Phase-filtered pick: `?phase=robber` serves only robber puzzles and
  ignores the placement mixture.
- Nullable sections: empty games dir → form card degrades gracefully.

## 6. Effort & sequencing

Enrichment (elo.py + service.py + tests) ~0.25 day — **do this first,
ideally alongside HOSTING's accounts work since both touch the ratings
store**. Dashboard module + API + UI + tests ~1 day. Total **~1.25
days**. Day-one value with puzzle data alone (Form + Skills + skill-gap
leaks + drill links); the Cost card and richer leaks light up as
REVIEW/COACH/LAB ship — the payload is designed nullable so nothing
blocks on anything.
