# COACH_SPEC — live coaching in play-vs-bot

A chess teacher doesn't let you drop the piece and then tut — they grab
your wrist mid-air. Coach mode is that: with the toggle on, the engine
checks every move you're about to make, and if it's a mistake, it stops
you BEFORE the move happens and lets you think again. This design choice
(pre-move interjection, not post-move takeback) is the heart of the
spec, so its rationale comes first.

## 1. Why interjection, not takeback (decided)

A takeback AFTER a move is applied is poisoned in Catan:
- your robber move may have STOLEN a specific card — you saw it; undo
  leaks it;
- ending your turn triggers the bot's response and your next roll —
  undoing rewinds public randomness both sides observed;
- the engine's replay machinery could technically rewind (games are
  deterministic from the log), but every rewind either leaks hidden info
  or re-rolls chance, and both feel like cheating.

Interjection has none of these: the questionable action is NEVER applied
until confirmed — no dice thrown, no cards stolen, nothing revealed. It
is also pedagogically stronger: the thinking happens at decision time.
(True takebacks stay rejected for v1; revisit only as a casual-mode
gimmick, never in rated ladder games.)

## 2. UX

- Toggle in the play-mode top badges: `🎓 Coach` (off by default;
  remembered per session; new-game keeps the last setting).
- With coach ON, a move you submit either:
  - **applies normally**, and the game-log line gets a small passive
    verdict badge (`✓ best`, `· fine`) — zero extra cost, reinforcement
    for free (§4); or
  - **bounces as an interjection card** (replaces the staged bar):
    - severity only, no spoiler: "⚠️ That loses about 12% win
      probability. Want to think again?"
    - three buttons: **Think again** (dismiss, position unchanged),
      **Hint** (reveals the CATEGORY of the engine's best move — "the
      engine prefers a different robber target" / "…a build" — one tier,
      logged), **Play it anyway** (confirms; applies; the log badge
      shows the true verdict).
- Severity thresholds (setting, default "normal"):
  - strict: flag regret ≥ 0.05 · normal: ≥ 0.10 · blunders-only: ≥ 0.15.
- Re-picking after an interjection is INSTANT-graded (the evaluation of
  the whole decision is cached — §3), so the flow never double-waits.
- Setup placements and discards are coached like everything else — a
  placement blunder is the most valuable interception in the game.

## 3. Server design (`trainer/play.py` extension)

- `PlaySession` gains, when coach is on:
  - `self.coach = MCTSEngine(simulations=COACH_SIMS,
    determinizations=4, seed=fixed, net=<same checkpoint as the bot>)`
    with `begin_game(self.human)` and `observe()` called in `_apply` —
    its CardTracker gives the coach the HUMAN's information set (same
    fairness rule as REVIEW_SPEC: the coach judges what you could know,
    it never peeks at the bot's hand).
  - `self._coach_cache: (n_actions, evals) | None` — evaluations of the
    CURRENT decision point, keyed by action count so it invalidates on
    any application.
- `act(...)` flow with coach on:
  1. Resolve the intended action (codec or discard multiset) — reject
     illegal as today.
  2. Get `evals` for the current position (cache hit, else
     `coach.evaluate(state)` — this is the ~1s the mode costs; it hides
     acceptably inside the existing "engine is thinking" affordance).
  3. `regret = q_best - q_chosen`. If `regret >= threshold` and the
     request lacks `"confirm": true` → return
     `{"coach": {"regret": r, "severity": "mistake"|"blunder",
       "hint_category": <category of best move — sent but UI reveals
       only on Hint click>}}` — **state untouched, no events emitted**.
  4. Otherwise apply as today; attach `ev["verdict"]` (points-band name
     from the cached eval) to the action's event for the log badge, plus
     `ev["coached"]: "confirmed"` when it was a played-anyway.
- Toggle: `POST /api/play/act {"coach_set": true|false}` flips it
  mid-game (evaluations simply start/stop); `GET /api/play/new?coach=1`
  starts on.
- Budget: `COACH_SIMS=160` default (same as the bot's play budget —
  the coach is exactly as strong as the opponent, which is honest and
  fast); `--coach-sims` server flag. Note asymmetry with review (512):
  the review may downgrade a move the coach passed — that's fine and
  worth a line in the review UI ("deeper analysis").
- Coach events logged to the session record (`log` entries gain
  `coached` fields) → the weakness dashboard later reads interjection
  rate, played-anyway rate, hint usage.

## 4. The passive badges (free value)

With coach on, every non-flagged human move already has an eval in the
cache — so every log line gets `✓ best / ✓ great / · fine` at zero extra
compute. This is deliberately understated: the loud path is reserved for
mistakes. (With coach OFF nothing is evaluated and no badges appear —
play mode stays exactly as fast as today.)

## 5. Client changes (`index.html`, ~120 lines)

- Coach toggle badge; persist in `localStorage`.
- `playAct` response branch: `res.coach` → render interjection card in
  the staged bar (severity sentence + three buttons); "Play it anyway"
  resends the same body + `confirm: true`; "Hint" reveals
  `hint_category` locally (no round-trip) and logs via the next act.
- Log badges: `appendEvents` renders `ev.verdict` as a suffix chip.
- The interjection must also work for discards (resend the same
  multiset + confirm).

## 6. Tests (`tests/test_coach.py`)

- Forced-blunder fixture: a position where one legal move is a known
  large regret (construct via a tiny net-free engine and a position with
  a dominant move; assert the dominated move triggers `coach` and the
  state/log are UNCHANGED — the no-side-effect property).
- Confirm path: same action + confirm applies, event carries
  `coached: "confirmed"` and a verdict.
- Cache: two acts at one decision → one evaluation (count via a
  monkeypatched evaluate).
- Toggle mid-game; badges appear only when coach on; discard
  interjection round-trip.

## 7. Effort & sequencing

Server ~120 lines, client ~120, tests ~80 — **~1 day**. No dependency
on EXPLAIN_SPEC (severity + category hint need no facts), but once
explanations exist, the interjection's Hint tier can upgrade to a
facts sentence. Pairs naturally with the bot ladder (ROADMAP D4):
coach available in casual rungs, disabled in rated ones.
