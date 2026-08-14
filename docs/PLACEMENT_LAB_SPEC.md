# PLACEMENT_LAB_SPEC — drill the draft until it stops losing you games

Setup placement is 1v1 Catan's opening theory: four decisions that fix
most of a game's production trajectory. The lab is a dedicated drill
mode: endless fresh boards, you play your seat's full draft against the
engine, every pick graded live with an explanation, and — the part no
other tool has — longitudinal bias analytics ("you systematically
overvalue ore and underweight expansion room").

## 1. The drill loop (UX)

- Third mode button: `🧪 Placement lab` (alongside Trainer / Play).
- A drill = one fresh board (strict_product_mode generator), one seat
  (alternating per drill). The 1v1 draft is a snake: A-settlement+road,
  B, B, A — so the user makes 2 settlement picks + 2 road picks; the
  engine plays the other seat (its picks animate via the existing
  playback path).
- Prompt names the slot: "Pick 1 of 2 — first settlement (A1)" etc.
- Click a vertex/edge → the pick applies immediately (play-mode idiom)
  → a **grade card** slides into the sidebar:
  - verdict chip from regret (reuse trainer point bands: Best / Great /
    Good / Inaccuracy / Mistake / Blunder),
  - "regret 0.04 · better than 78% of legal picks" (percentile over the
    ranked eval list — softens the sting of coarse bands),
  - one explanation sentence (EXPLAIN_SPEC `render`, best-vs-chosen),
  - a top-3 table with Q values, chosen row highlighted (the trainer's
    result-table idiom).
  - board marks: your pick (blue ring), engine's best (green ring).
- "Continue →" advances (opponent picks play out), next slot.
- Drill end (setup phase exits): summary card — four grades, drill
  regret total, "Next board" button.
- A persistent **Lab stats** card (see §4) sits under the summary.

## 2. Grading (server)

- `LabService` (new `trainer/lab.py`), sessions like `PlayService` but:
  the game ENDS when `state.phase` leaves SETUP; every human action is
  evaluated BEFORE being applied.
- Grade = `MCTSEngine(simulations=LAB_SIMS, determinizations=LAB_DETS,
  seed=fixed, net=<champion>).evaluate(state)` on the pre-move state;
  `regret = q_best - q_chosen`; percentile = rank of chosen among all
  legal moves by Q.
- Budget: `LAB_SIMS=256, LAB_DETS=4` default (single seed — drills want
  fast coarse feedback, not admission-grade stability; puzzle labeling
  keeps its 3-seed rigor). Expect ~2–4 s per grade on the old machine;
  expose `--lab-sims`. UI shows "engine grading…" on the card skeleton.
- No hidden-info subtlety during setup (no hands, no dev cards), so
  determinization is near-degenerate there — cheap.
- Board seeds: **ledger range 400000+** (record `next free` in
  HANDOFF.md when consumed). Every drill's seed is stored, so any drill
  can be replayed or re-graded deeper later.

## 3. Data model

Append-only JSONL `data/lab_attempts.jsonl` (per-user table once
HOSTING's accounts land — same Store seam):

```json
{"drill": "d400012-1", "seed": 400012, "seat": 0, "slot": "A1",
 "kind": "settlement", "chosen": 17, "best": 31,
 "regret": 0.041, "pct": 0.78,
 "f_chosen": {"pips": 10, "div": 3, "port": null, "ore_share": 0.4, "spots": 2},
 "f_best":   {"pips": 12, "div": 4, "port": "2:1 wheat", "ore_share": 0.25, "spots": 3},
 "ts": "..."}
```
`f_*` are the compact per-pick facts (EXPLAIN_SPEC `move_facts` subset:
total pips, diversity, port, per-resource share, expansion spots). If the
lab ships before EXPLAIN_SPEC, inline this subset — it's ~30 lines over
`_resource_pips`/`_port_type_at`/`_legal_settlement_vertices` — and the
explanation sentence degrades to the generic Q-gap line until `render`
exists.

## 4. Bias analytics (the differentiator)

Computed over the last N=50 graded settlement picks (roads later):

- **Per-slot skill**: mean regret for A1 / B1 / B2 / A2 separately —
  second-pick judgment (reading the opponent's board) is a distinct
  skill and usually the weaker one.
- **Systematic deltas** (chosen minus engine-best, averaged):
  `Δpips`, `Δdiversity`, `Δore_share` (and each resource),
  `Δexpansion_spots`, port-pick rate vs engine's. A bias exists when
  |mean Δ| > threshold with n ≥ 15.
- **Rendered statements** (threshold rules, max 3 shown):
  - Δore_share > +0.08 → "You overvalue ore-heavy spots."
  - Δdiversity < −0.5 → "You give up resource diversity the engine keeps."
  - Δspots < −0.7 → "You pick tight spots; the engine keeps expansion room."
  - Δpips < −1.5 → "You leave raw production on the table."
  - slot mean regret A2/B2 ≫ A1/B1 → "Your SECOND pick is the leak —
    drill boards where the best seats are taken."
- Endpoint `GET /api/lab/stats` returns counts, per-slot means, deltas,
  statements; the stats card renders it. Statements must derive ONLY
  from the stored deltas (same honesty rule as EXPLAIN_SPEC).

## 5. API

- `GET /api/lab/new` → drill view (same shape as play views: layout,
  board, context, moves, prompt, `slot`).
- `POST /api/lab/act {drill, codec_id}` → `{grade: {...}, events: [...],
  next: <view>|null, summary: {...}|null}` — grade for the pick just
  made, opponent events for playback, then the next decision or the
  drill summary.
- `GET /api/lab/stats` → §4 payload.

## 6. Tests (`tests/test_lab.py`)

- Full drill with a tiny engine: exactly 4 human decisions graded
  (2 settlements + 2 roads), grades well-formed (regret ≥ 0, pct in
  [0,1], chosen/best are legal ids), drill terminates at setup exit.
- Facts subset arithmetic on a constructed board (pips/diversity/port
  exact values).
- Stats: synthetic attempts file → per-slot means and delta statements
  match hand-computed values; below-n thresholds emit nothing.
- Attempts survive restart (file append + reload).

## 7. Effort & order

`trainer/lab.py` ~180 lines; routes ~20; UI ~200 (mode button, grade
card, stats card — heavy reuse of playback/marks/result-table idioms);
tests ~100. **~1.5–2 days.** Best sequenced after EXPLAIN_SPEC (grade
cards get real sentences) and after HOSTING if per-user stats matter
from day one — but a local single-user v1 has no hard dependency on
either.
