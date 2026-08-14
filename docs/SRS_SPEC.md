# SRS_SPEC — spaced repetition on missed puzzles

Chessable's insight applied here: a puzzle you failed is the most
valuable puzzle in the library, and it should come back — at expanding
intervals, until you own it. Smallest feature in the product plan,
disproportionate learning value.

## 1. Scheduling model (decided: Leitner boxes, not SM-2)

Five boxes with fixed intervals; no per-item ease factors (SM-2's knobs
are overkill for a pool this size and make behavior harder to test):

| box | interval until due |
|---|---|
| 1 | 1 day |
| 2 | 3 days |
| 3 | 7 days |
| 4 | 16 days |
| 5 | 35 days → graduation on pass |

- **Entry**: a RATED trainer attempt scoring **< 75 points** enqueues
  the puzzle in box 1. (75 = the "great" band floor; near-misses are
  still worth re-seeing.) Lesson/lab/review attempts never enqueue —
  only the rated trainer path.
- **Review pass** (≥ 75): promote one box; passing in box 5 graduates
  (removed, with a lifetime `graduated` count kept for the dashboard).
- **Review fail** (< 75): back to box 1, `lapses += 1`.
- **Reviews are UNRATED for puzzle-Elo** — repeat exposure would
  corrupt the rating (same isolation rule as lessons).
- **Cap**: 200 active items. When full, new misses evict the
  lowest-box, most-lapsed, oldest item (they'll re-enter when missed
  again organically). Prevents unbounded review debt.
- Memorizing the answer is PARTLY the point (pattern internalization),
  but boards are distinctive — note in the UI copy: "seen this before?
  good — prove it."

## 2. Data model

In the ratings store (per-user via the HOSTING seam):

```json
"srs": {
  "a5c06efd939a": {"box": 2, "due": "2026-08-15T00:00:00Z",
                    "lapses": 1, "added": "..."},
  "_graduated": 14
}
```

Time handling: all comparisons through one injectable
`now()` (module-level, monkeypatchable) — the tests freeze it; never
call `datetime.now` inline in scheduling logic.

## 3. Server flow

- Enqueue: inside the trainer submit path, after `Ratings.record`,
  when `rated and rated_points < 75` → `srs.add(pid)`.
- `GET /api/srs/summary` → `{due: 7, active: 31, graduated: 14}` —
  cheap; the trainer header polls it on load.
- `GET /api/srs/next` → present the earliest-due puzzle (reuses
  `service.present`), tagged `"srs": true` in the payload.
- `POST /api/submit {..., srs: true}` → scored normally for DISPLAY
  (points, table, explanation all show), but recorded ONLY to the SRS
  state (box move), never to Elo. Response gains
  `srs_result: {box_before, box_after, graduated, due_next}`.
- Composite placement puzzles keep their road stage in reviews; the
  pass check uses the same `rated_points` composite as the original.

## 4. UI

- Trainer header chip: `🔁 Review (7)` — hidden at 0 due. Click →
  review session: same trainer surface with a queue counter ("3 of 7"),
  the box/next-due shown after each submit ("moved to box 3 — back in
  7 days"), and a done screen ("queue clear — come back tomorrow").
- A small line in the dashboard's Form card: active/graduated counts.
- ~80 lines client.

## 5. Tests (`tests/test_srs.py`)

- Entry: rated miss enqueues; rated pass doesn't; unrated paths
  (lesson/srs itself) never enqueue.
- Box math: pass promotes, fail demotes to 1, box-5 pass graduates and
  increments the counter (frozen clock walks the due dates).
- Elo isolation: an SRS submit changes no rating state (regression
  guard).
- Due ordering: earliest-due served first; empty queue → summary 0 and
  `next` 404s cleanly.
- Cap + eviction rule fires deterministically.

## 6. Effort & sequencing

Server ~90 lines, client ~80, tests ~80 — **~0.75 day**. No
dependencies (works on today's enriched attempt log). ROADMAP already
sequences it FIRST among the product-vision features precisely because
it's the cheapest win; ship it in the same batch as the accounts work
so the queue is per-user from birth.
