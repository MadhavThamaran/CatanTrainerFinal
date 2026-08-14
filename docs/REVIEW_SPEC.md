# REVIEW_SPEC — post-game review ("game report") for play mode

The chess.com-style report card after a game against the bot: every one
of your decisions scored by the engine, blunders called out, a win-prob
graph of the whole game, click any move to see the position and what the
engine preferred. All the machinery exists (deep evaluation, regret →
points, board rendering with marks); this spec is the assembly manual.

## 0. Foundation this feature stands on (facts, not choices)

- Games are **fully deterministic from (seed, action log)** — engine
  guarantee since M1 (steal outcomes, dice, dev draws all come from the
  state's seeded RNG). A stored action list replays the entire game
  exactly, reconstructing every intermediate position.
- `MCTSEngine.evaluate(state, viewer)` returns ranked `(action, q,
  visits)` for ALL legal actions — including discards (which the puzzle
  codec can't express but review can serve directly).
- Fairness: evaluating with a `CardTracker` for the human seat gives the
  reviewer exactly the information set the human had — no hindsight
  peeking at the bot's hand. The tracker is driven by replaying
  `observe()` over the log, the same way the bot maintains it live.
- Regret → points → verdict bands already exist in `puzzles/scoring.py`.

## 1. UX

- Game ends → the sidebar Game log card gains a **"📊 Review game"**
  button.
- Clicking it starts an async review; the card shows a progress bar
  ("scoring decision 12/38…") and results stream in as computed.
- Review view (replaces the log list):
  - **Header**: accuracy score + verdict chips: `⭐ Best ×12 · ✓ Good ×9 ·
    ⚠ Inaccuracy ×4 · ✗ Mistake ×2 · ⛔ Blunder ×1`.
  - **Win-prob graph**: SVG polyline of your win probability at each of
    your decisions (root Q of the engine's best move). Blunders get red
    dots; clicking a dot selects that move.
  - **Move list** (chronological): each row = verdict icon, your move
    label, points, and for non-best moves the engine's move. Clicking a
    row renders that position on the MAIN board (from the row's stored
    board snapshot) with two marks: your move (blue ring) and the
    engine's best (green ring) — the exact marks idiom the trainer's
    post-submit view already uses.
  - "↩ Back to game" restores the final position; "🔄 New game" stays.

## 2. Data model

### 2a. Game records (prerequisite — small change to `trainer/play.py`)
`PlaySession` additionally records `self.seed` and `self.log`: a list of
`(actor, action_dict)` for every applied action, where `action_dict` is a
faithful serialization of the frozen `Action` dataclass:
`{"type": t.name, "player": p, "vertex": v, "edge": e, "hex": h,
"give": give.value?, "get": get.value?, "resources": [r.value...]?}`
(deserializer: `ActionType[name]` + `Resource(value)`).
On game over, write `data/games/<sid>.json`:
`{"seed": ..., "human": seat, "log": [...], "winner": ..., "final_vp":
[a, b], "bot": net_path, "ts": iso}`.
This must land BEFORE games become reviewable — games played without a
log cannot be reviewed. It also becomes "game history" once accounts
exist.

### 2b. Review result rows (streamed)
```json
{"i": 0, "nth_decision": 7, "turn": 12,
 "chosen": {"label": "Build road at 6O/9S", "q": 0.44, "points": 40},
 "best":   {"label": "Buy development card", "q": 0.49},
 "regret": 0.05, "verdict": "good",
 "win_prob": 0.49,
 "board": { ...board_state()... },
 "marks": [{"kind":"edge","target":17,"chosen":true},
            {"kind":"button","target":null,"best":true}]}
```
`board` is `service.board_state(state_at_decision, human)` (~5 KB; ~50
rows ≈ 250 KB total — fine). `marks` reuse the trainer's ring renderer;
kind "button" moves (trades, dev plays, discards) simply have no board
mark and are list-only.

## 3. Scoring

- Engine: `MCTSEngine(simulations=REVIEW_SIMS, determinizations=REVIEW_DETS,
  seed=fixed, net=<same checkpoint the game was played against>)`, with
  `begin_game(human_seat)` and `observe()` replayed so determinization is
  exact-belief. Defaults `REVIEW_SIMS=512, REVIEW_DETS=6` (≈3–8 s per
  decision on the old laptop; expose `--review-sims` on the server).
- Score only HUMAN decision points with >1 legal action (forced moves and
  the bot's moves are replayed, not scored). Include discards.
- Per decision: `evals = engine.evaluate(state, viewer=human)`;
  `q_of(chosen)` from the eval list (chosen is always present — it was
  legal); `regret = q_best - q_chosen`;
  `points = points_for_regret(regret)`; `win_prob = q_best`.
- Verdicts by points: 100→best, 75→great, 40→good, 10→inaccuracy,
  0→mistake, −25→blunder.
- **Accuracy** (header number): `mean(max(points, 0))` over scored
  decisions — simple, monotone, explainable ("average move quality,
  blunders floor at 0").

## 4. API

- `POST /api/review/start {game_id}` → `{review_id, total}` where total =
  count of scorable decisions (computable in one fast replay pass without
  evaluation). 409 if a review is already running (single worker).
- `GET /api/review/poll?id=...&from=N` → `{done: bool, total, results:
  [rows N..]}` — client polls every ~1.5 s, appends rows.
- Results are also cached to `data/games/<sid>.review.json` on completion
  so a finished review reloads instantly.

## 5. Worker algorithm (background thread in `trainer/review.py`)

```
load game record; state = new_game(seed)
reviewer = MCTSEngine(...); reviewer.begin_game(human)
for (actor, action_dict) in log:
    action = deserialize(action_dict)
    if actor == human and len(legal_actions(state)) > 1:
        evals = reviewer.evaluate(state)          # BEFORE applying
        emit row (chosen matched by == against evals' actions;
                  board_state snapshot; marks; regret; verdict)
    apply_action(state, action)
    reviewer.observe(state, action)
assert replayed final VPs == record.final_vp     # replay-integrity check
```
The `==` match works because `Action` is a frozen dataclass with value
equality. The final assert catches any serialization drift loudly.

## 6. Edge cases (decided)

- **Draw/action-cap games**: reviewable; winner field says draw.
- **Session evicted or server restarted**: review works from the disk
  record; the Game log card lists recent records (`data/games/`) so old
  games are reviewable too.
- **Concurrent reviews**: one at a time; the button greys with "review
  in progress".
- **Discards**: scored and listed (custom label from play.py's `_label`),
  no board mark.
- **Setup placements**: scored like everything else — placement review is
  some of the most instructive content.
- **Engine disagreement with itself**: review re-evaluates with a fixed
  seed at higher budget than play, so "the bot blundered by its own
  review" WILL appear (play runs 160 sims, review 512). That's honest —
  show bot rows? **No** — v1 reviews only the human (the bot's rows would
  double runtime; revisit later as a toggle).

## 7. Implementation plan (file by file)

1. `trainer/play.py` — record seed/log; serialize/deserialize helpers;
   write `data/games/<sid>.json` at game over. (~60 lines)
2. `trainer/review.py` — worker thread + ReviewService (start/poll,
   single-flight lock, disk cache). (~140 lines)
3. `trainer/server.py` — two routes; `--review-sims` flag. (~25 lines)
4. `trainer/static/index.html` — review button + progress bar; results
   list; win-prob SVG polyline; row-click → renderBoard(snapshot, marks);
   back button. (~160 lines; reuse `renderBoard`'s marks path — it
   already draws best/chosen rings from `targetXY`.)
5. `tests/test_review.py` — play a scripted game (tiny heuristic bot),
   review at sims=16: row count == scorable decisions; every row's
   chosen label matches the played action; regret ≥ 0; verdict mapping;
   replay-integrity assert passes; review survives service restart (disk
   record). (~90 lines)

Estimated effort: ~1 day for a competent coding model, all
machine-independent. No new dependencies.

## 8. Acceptance

- Finish a game → Review → progress streams → header shows accuracy +
  chips; graph renders; clicking a blunder shows the position with your
  move and the engine's move marked; reload the page → finished review
  loads from cache; `pytest tests/test_review.py` green.
