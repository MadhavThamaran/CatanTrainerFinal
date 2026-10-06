# LADDER_SPEC — the bot ladder ("beat your way up")

Play mode has one opponent at one strength. The ladder turns the archive
of training generations into a progression: named rungs from pushover to
boss, unlocks, a play-Elo earned in rated games, and per-rung records.
The strength ladder already exists as a side effect of the flywheel —
this spec is about packaging it honestly.

## 1. The rungs (initial definition)

A rung = (agent type, checkpoint, sims, dets) + a name + a provisional
rating. Eight rungs spanning everything measured:

| # | name | config | provisional Elo | anchor evidence |
|---|---|---|---|---|
| 1 | Settler | HeuristicAgent (no search) | 800 | loses 15/100 to raw MCTS-160 |
| 2 | Apprentice | raw MCTS s=32 k=2 | 1000 | interpolated |
| 3 | Journeyman | raw MCTS s=160 k=4 | 1200 | the M4 Stage-B engine |
| 4 | Veteran | gen5 s=64 k=3 | 1400 | interpolated |
| 5 | Expert | gen5 s=160 k=4 | 1550 | gen5 = 63/100 vs rung 3 |
| 6 | Master | gen7 s=160 k=4 | 1700 | gen7 = 75/100 vs rung 3 |
| 7 | Grandmaster | gen7 s=400 k=4 | 1850 | sims scaling, unmeasured |
| 8 | The Engine | gen7 s=800 k=6 | 2000 | boss; ~4–6 s/move — say so in UI |

- `HeuristicAgent` already implements the same Agent interface
  (`begin_game/observe/select_action`) the play session calls — rung 1
  is a config change, not new code.
- Provisional Elos are DECLARED GUESSES. The calibration script (§5)
  replaces them with measured values; until then the UI marks ratings
  "provisional".
- Rung configs live in one table in `trainer/ladder.py` — adding a rung
  after a future promotion (gen9!) is a one-line append.

## 2. Progression & rating rules (decided)

- **Unlock**: rung N+1 unlocks on your FIRST win vs rung N (rated or
  casual). Rungs 1–3 start unlocked (nobody enjoys grinding a pushover).
- **Stars** (mastery, per rung): ⭐ = 1 win · ⭐⭐ = 3 wins · ⭐⭐⭐ = win
  rate ≥ 60% over your last 10 games there (min 10 games).
- **Play-Elo**: separate from puzzle-Elo. Updated only in RATED games,
  standard Elo vs the rung's FIXED rating (bots don't drift), K=24,
  start 1200. Draw (action-cap) = 0.5.
- **Rated vs casual**: chosen at game start. Rated = no coach, no hints,
  counts for Elo + stars. Casual = coach allowed (COACH_SPEC), still
  counts toward unlocks (a win is a win) but not Elo/stars.
- **No demotion**: unlocks are permanent; Elo carries the bad news.

## 3. Server design

- `trainer/ladder.py`: `RUNGS` table; `make_bot(rung) -> Agent`
  (HeuristicAgent | MCTSEngine with/without net — the get_evaluator
  lru_cache holds 4 models; the ladder uses at most 3 distinct
  checkpoints, fine); ladder state read/write through the same store
  seam as ratings (per-user once HOSTING lands):
  `{"play_elo": 1200.0, "rungs": {"3": {"w": 5, "l": 2, "stars": 2,
  "unlocked": true, "last10": [1,0,1,...]}}}`.
- `PlayService.new_game(seed, rung=6, rated=False)` — resolves the bot
  from the table; session records `rung`, `rated` (game records gain the
  fields; REVIEW works unchanged).
- On game over (`_persist` moment): if rated, update play-Elo + rung
  record + stars; always update unlocks. Return the deltas in the final
  view so the UI can toast "Play rating 1214 → 1231; ⭐⭐ on Master".
- Latency guard: rung 8's ~5 s/move × ~60 bot decisions ≈ 5 min of
  total thinking per game — the UI banner for rungs 7–8 says "this
  opponent thinks slowly", and the existing playback pacing absorbs it.

## 4. Client changes

- Play mode gains a pre-game screen (replaces auto-new-game): rung cards
  in a column — name, icon, record (W–L), stars, provisional-Elo badge,
  locked rungs greyed with "beat <previous> to unlock" — plus a
  rated/casual toggle and Start. `localStorage` remembers the last rung.
- In-game: header badge shows rung name + rated/casual; game-over view
  shows Elo/star deltas; "Rematch" and "Next rung →" (when unlocked)
  buttons.
- ~150 lines; the card/list idioms all exist.

## 5. Calibration script (`scripts/ladder_calibrate.py`, offline)

Measured ratings beat guessed ones. Round-robin adjacent rung pairs
(and each rung vs rung 3 as a common anchor), 60 games/pair via the
`net_vs_net`/`mcts_eval` harness patterns; fit ratings by minimizing
Σ (observed − expected(Elo))² with rung 3 pinned at 1200 (simple 1-D
iterative fit, no dependencies). Writes `data/ladder_calibration.json`;
`RUNGS` loads measured ratings when the file exists. ~8–10 h of bot
games — run it on the big machine overnight, once. Also VALIDATES
monotonicity: if a rung measures out of order (plausible for 4 vs 5),
reorder the table before shipping.

**As built:** pair i plays seeds `--seed-offset + 1000·i + game` (default
offset 91,000,000, so a run consumes 91,000,000–91,011,059 — reserved in
CLAUDE.md's seed ledger); each finished pair is saved to
`data/ladder_calibration_results.json`, so `--resume` (with the SAME
offset) replays only what is missing. `trainer/ladder.py` reads the
ratings file once at import: restart the server after a run.

## 6. Tests (`tests/test_ladder.py`, `tests/test_ladder_calibrate.py`)

- Config resolution: every rung builds an agent; rung 1 has no net.
- Unlock/star/Elo math on scripted outcomes (hand-computed fixtures).
- Rated-only Elo movement; casual wins unlock but don't rate.
- Persistence round-trip; game records carry rung/rated.
- A full tiny-budget game vs rung 1 terminates (reuses test_play
  scaffolding).
- A measured rating replaces the provisional guess everywhere it is read
  (`rating_for`, the rung table, play-Elo scoring); the suite pins
  `ladder._CALIBRATED` to `{}` so a calibrated machine can't change it.
- The calibration script, with a stand-in for the games: default run stays
  inside the ledger range with no seed shared by two games, `--resume`
  replays only missing pairs on their original seeds, the output loads as
  the ladder's measured ratings, and the fit recovers known ratings.

## 7. Effort & sequencing

Server ~150 lines, client ~150, calibration script ~120, tests ~100 —
**~1.5 days** + one overnight calibration run. Depends on nothing;
pairs with COACH_SPEC (rated games disable coach) and feeds
DASHBOARD_SPEC's Form card (W/L becomes per-rung). Sequence after
review/coach so rated games have full telemetry from day one.
