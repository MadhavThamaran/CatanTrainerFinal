# CURRICULUM_SPEC — structured lessons ("learn the game in order")

Puzzles teach pattern recognition; they don't teach concepts a beginner
doesn't have yet. The curriculum is ordered tracks: a short authored
explainer, then a curated drill set from the existing puzzle library,
then a pass bar. Content is the long pole — the code is a thin loader.

## 1. Track list (v1 — six tracks, ordered)

| # | track | drills from | core concepts the explainer must cover |
|---|---|---|---|
| 1 | Production math | placement (easy band) | pips per number (the 1-5-1 pyramid), expected income per roll, why 6/8 ≠ safe, robber exposure |
| 2 | The draft | placement + Placement-lab link | complementary picks, diversity vs specialization, port-seat tradeoffs, reading the opponent's first pick (B2/A2 dynamics) |
| 3 | Robber & the seven | robber | discard planning above 7 cards, target selection (deny their engine, not their count), Friendly-Robber fallback rules |
| 4 | Port economics | trade | effective ratios, when 4:1 is right, trade-toward-build tempo, monoculture + 2:1 as a strategy |
| 5 | Development cards | devcard | buy timing, knight timing vs LA race arithmetic, why VP cards change the endgame count, Road Building / YoP / Monopoly spots |
| 6 | The race | endgame | counting turns-to-15 for both sides, LR/LA swing math, when to block vs when to sprint |

Rules facts in explainers MUST be verified against `docs/rules.md` /
`docs/1v1_Colonist_Inspired_Spec.md` (this variant differs from base
Catan: 15 VP, no player trades, friendly robber, balanced dice — an
explainer that teaches base-Catan lore is a bug).

## 2. Content format (authored, in-repo)

`content/lessons/<nn>-<slug>.md` with frontmatter:

```yaml
---
id: robber-and-seven
title: Robber & the seven
order: 3
pages: 3                # split on '---' hrules into swipeable pages
drills:
  phase: robber         # tag filter into the live puzzle set
  count: 8
  difficulty: [easy, medium]
  pinned: [a5c06efd939a, ...]   # optional curated puzzle ids, served first
pass: {avg_points: 75, min_attempted: 8}
---
<markdown pages...>
```

- Pinned ids let a human curate exemplary puzzles; the rest fill from
  the tag/difficulty filter, EXCLUDING puzzles the user has already
  seen rated (freshness), deterministic per (user, lesson) so retries
  see the same set.
- Loader validates at startup: unique ids/orders, referenced pins exist
  in the live set, drills.phase is a known tag; a broken lesson fails
  loudly in tests, not silently in prod.
- Markdown rendering: the page already has an HTML pipeline; keep the
  subset tiny (headings, bold, lists, inline board-notation code). No
  external renderer dependency.

## 3. Progression & scoring (decided)

- **Lesson drills are UNRATED for puzzle-Elo.** Curated easy sets would
  inflate the rating, and repeatability matters for learning. Attempts
  are logged in lesson state only (the submit API gains a
  `lesson: <id>` flag routing recording away from `Ratings.record`).
- Pass = `avg_points ≥ pass.avg_points` over `min_attempted` drill
  puzzles in one run. Fail → retry with the same set (repeatability is
  a feature; the answers being learnable is the point).
- Track completion is per-user (store seam), shows as a badge row;
  tracks unlock in order (1 always open; N+1 opens on passing N —
  skippable via a "let me test out" link that just runs the drill).

## 4. API + UI

- `GET /api/lessons` → list with per-user state (locked/passed/score).
- `GET /api/lessons/<id>` → pages + resolved drill puzzle ids.
- `POST /api/submit {..., lesson: id}` → scored as today, recorded to
  lesson state, response gains `lesson_progress: {attempted, avg,
  passed}`.
- UI: `📚 Learn` mode button → track list (cards with badges) → lesson
  view: page dots, prev/next, then "Start drills (8)" → the normal
  trainer surface with a lesson progress bar instead of the rating
  badge → pass/fail screen with retry.
- ~180 lines client, mostly reusing the trainer surface.

## 5. Authoring plan (the actual work)

Each explainer is 3 pages × ~150 words, written from the concepts table
in §1 + the variant rules docs. A capable model drafts all six in one
session (~2–3 h including fact-checking against the rules docs); a human
pass for tone. The spec deliberately keeps mechanics (loader, pass bar)
independent of content quality so drafts can ship and iterate.

## 6. Tests (`tests/test_curriculum.py`)

- Loader: valid fixture parses; duplicate order / unknown phase /
  missing pin each fail with a clear error.
- Drill resolution: pinned first, filter fills, excludes rated-seen,
  deterministic per (user, lesson).
- Pass math incl. boundary (avg exactly 75); retry reuses the set.
- Elo isolation: lesson submits move NO ratings state (regression
  guard against inflation).
- Unlock ordering + test-out path.

## 7. Effort & sequencing

Loader + API + state ~0.75 day; UI ~0.75 day; content drafting ~0.5
day; tests ~0.5 day → **~2.5 days**, the most content-bound feature.
Sequence AFTER explanations (EXPLAIN_SPEC) — lesson drills with "why"
feedback teach twice as well — and after HOSTING if completion should
be per-account from day one.

## 8. As built

Shipped 2026-10-06: `trainer/curriculum.py` (loader, drill resolution, run
state), `TrainerService.lessons_view/lesson_view/next_lesson_drill/submit_lesson`,
the routes below, the `📚 Learn` client, six lessons in `content/lessons/`, and
`tests/test_curriculum.py`. Where it differs from the plan above:

- **API.** `GET /api/lessons`, `GET /api/lessons/<id>` (rendered pages + drill
  spec, no puzzle ids), `GET /api/lessons/<id>/next` (the next unanswered drill;
  the server owns the set, like `/api/srs/next`) and `lesson: <id>` on
  `POST /api/submit` (`lesson` together with `srs` is a 400). All need login.
  The list also carries `lab_available`, so `link: lab` shows only where a lab
  exists (not on `--no-play` deployments). `--lessons DIR` on the server.
- **Runs.** A run is one pass through the drill set. It is scored when every
  drill is answered (average >= `pass.avg_points` over at least
  `pass.min_attempted`; exactly the bar passes) and then closed, so the next
  `/next` is a retry on the SAME set. A half-finished run resumes. `passed` is
  sticky; `runs`, `best_avg`, `last_avg` and `tested_out` are kept. The set is
  resolved at the first `/next` and stored, so "not already rated" is judged once
  and retries are identical; editing a lesson's filter or pins re-resolves it.
- **Unrated, structurally.** `present(..., unrated=True)` creates no rating entry
  and ships no ratings; `submit_lesson` never calls `Ratings.record`, never
  enqueues SRS, and rejects puzzles outside the drill set. `Ratings.lessons` rides
  in the existing per-user blob. "Seen" for freshness means rated (attempts > 0),
  not merely viewed.
- **Frontmatter.** A subset parser, no YAML dependency (scalars stay strings, so a
  numeric-looking puzzle id is safe). Unknown keys, a pages-count mismatch, an
  unreachable `count` and `min_attempted > count` fail at load. Optional
  `link: lab`; defaults: count 8, difficulty `[easy, medium]`, pass 75 / count.
  Markdown renders server-side to escaped HTML (headings, bold/italic, lists, code).
- **Content.** Six 3-page drafts (about 100-170 words a page), written against
  `docs/rules.md` and the engine's constants. Notably the discard limit is *above
  9* (the §1 table says 7, which is base Catan). A tripwire test checks the text
  for base-Catan lore. They want the human tone pass §5 calls for. Easy puzzles
  are scarce in `trade` (6) and `devcard` (9), so those tracks draw from
  easy+medium; only track 1 is limited to the easy band.
- **Scoring notes.** A 75 average is about 6-7 of 8 best-move picks (a miss
  usually scores 0-40 or -25). Placement drills use the trainer's composite score
  (settlement + road); a random road after the best settlement still averages
  about 88-91, so the bar is fair there.
