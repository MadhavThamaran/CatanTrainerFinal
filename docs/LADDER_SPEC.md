# LADDER_SPEC — the bot ladder ("beat your way up")

Play mode has one opponent at one strength. The ladder turns the archive
of training generations into a progression: named rungs from pushover to
boss, unlocks, a play-Elo earned in rated games, and per-rung records.
The strength ladder already exists as a side effect of the flywheel —
this spec is about packaging it honestly.

## 1. The rungs (initial definition — v1, superseded by §8)

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
  (v1 — in v2 no rung is slow: the deepest searches ~1.2 s per move, so
  `SLOW_RUNGS` is empty; §8.)

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

**First measurement (2026-10-06, 14.1 h):** the 8 declared rungs span only
~290 Elo (1134-1428). Two tiers (non-net 1-3, net 4-8); gaps inside a tier
are inside the ~±45 Elo noise of 60-game pairs; The Engine (sims=800) is not
measurably above Master (sims=160). Table and pair results: README,
"Measured: the ladder is far flatter than declared".

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

## 8. v2: rungs as strength dials (2026-10-07)

**Why.** The first calibration (README, "Measured: the ladder is far flatter
than declared") found the §1 rungs spanned ~290 Elo, not 1,200, and that
search depth bought almost nothing: The Engine (800 sims) was not measurably
above Master (160 sims). The §1 table is v1; spacing rungs by measurement
needs a dial that actually moves strength.

**The dial probe** (`scripts/ladder_dial_probe.py`, run from a frozen copy of the
code; seeds 93,000,000-93,009,999; gen-7 net; 80 games per setting against the
heuristic bot, rated on the anchor scale where the heuristic = 1149.4; results in
`data/ladder_dial_probe_results.json`, log in `data/ladder_dial_probe_report.txt`):

| setting (sims x dets, random-move rate) | wins/80 | Elo (95% CI) |
|---|---|---|
| 64 x 3, 0 | 54 | 1276 (1196-1357) |
| 32 x 2, 0 | 58 | 1318 (1233-1402) |
| 16 x 2, 0 | 53 | 1267 (1187-1346) |
| 8 x 1, 0 | 54 | 1276 (1196-1357) |
| 32 x 2, 0.10 | 40 | 1149 (1074-1225) |
| 32 x 2, 0.20 | 37 | 1123 (1048-1199) |
| 32 x 2, 0.30 | 22 | 981 (897-1065) |
| 32 x 2, 0.45 | 11 | 830 (722-939) |
| 32 x 2, 0.65 | 1 | 390 (230-693) |
| 32 x 2, 0.85 | 0 | 230 (230-622) |

Elo is floored at a 0.5% win rate (230), so the 230s are floors, not estimates:
0 wins in 80 only says the setting is below ~620.

1. **Search depth is flat.** 8 x 1 (one determinization, 8 simulations: the
   net's policy with a token search) is as strong as 64 x 3 within noise, and
   80 games take a minute instead of twelve.
2. **A random-move rate moves strength steeply and smoothly**, the first 10%
   costing ~130 Elo. Fitted: p(win vs heuristic) = 0.683 * (1 - eps/0.70)^1.52.

**The dial.** `agents/noisy.py::EpsilonAgent(base, epsilon)`: on a non-forced
decision, with probability epsilon play a uniformly random legal action instead
of the base's choice. Bank trades are excluded from the random pool (a bot that
bank-trades at random reads as churn, not as a mistake), forced decisions are
never touched, and the base's search is skipped when the random branch is taken,
so noisier rungs are also cheaper.

**The table** (declared targets = 100-Elo steps below The Engine, solved
through the fit; rungs 1-7 are one bot, gen-7 at 16 x 2, so a rung's move is
near-instant; only rung 8 searches):

| # | name | epsilon | target Elo | measured Elo |
|---|---|---|---|---|
| 1 | Settler | 0.55 | 687 | 545 |
| 2 | Apprentice | 0.49 | 787 | 657 |
| 3 | Journeyman | 0.41 | 887 | 831 |
| 4 | Veteran | 0.31 | 987 | 917 |
| 5 | Expert | 0.20 | 1087 | 1039 |
| 6 | Master | 0.09 | 1187 | 1188 |
| 7 | Grandmaster | 0 | 1287 | 1260 |
| 8 | The Engine | gen-7 at 160 x 4, 0 | 1387 (v1 measurement of that bot) | 1358 |

**Safeguards.**
- *The scale does not move.* `ladder.ANCHOR` (raw MCTS 160 x 4, the v1
  "Journeyman") is defined as 1200; calibration plays every rung against it, so
  v1 and v2 numbers are comparable.
- *A measured rating only applies to the bot it was measured on.*
  `rung_signature` (kind, net, sims, dets, epsilon) is stored beside the
  ratings, and `_load_calibration` drops any rung whose signature changed (and
  any file without signatures, i.e. v1's). `--resume` likewise reuses a saved
  pair only if both bots are unchanged, so editing one rung replays only its
  pairs.
- *Per-user rung records reset once.* They key on the rung number, so after a
  re-spec they would describe different bots; `LADDER_VERSION` resets
  W/L/stars/unlocks on load and keeps the play-Elo (same anchored scale).
- *Ordering noise is smoothed.* The dials make the rungs monotone by
  construction, so a fitted inversion between neighbors is sampling noise; the
  fit is followed by pool-adjacent-violators smoothing and any pooled rungs are
  reported.

**Status.** v2 calibrated 2026-10-07 (15 pairs x 60 games = adjacent rungs + every
rung vs the anchor; seeds 92,000,000-92,014,999; 2.4 h): measured 545 / 657 / 831 /
917 / 1039 / 1188 / 1260 / 1358, an 813-Elo monotone span against v1's 290 (the fit
found no inversions). Steps run 72-174, mean 116; one adjacent pair resolves ~+-47.
The bottom three rungs came out 56-142 below their targets, plausibly because the
dial fit was extrapolated through its sparsest probe points; kept as measured, since
the shown rating is the measured one and the mean step is inside the intended 100-150
band. The Engine is v1's Master bot: 1387 then, 1358 now, so the anchored scale held.
Results are in `data/ladder_calibration*.json` and `data/ladder_calibration_report.txt`;
the pair table with CIs is in the README ("Measured: the dials work").
