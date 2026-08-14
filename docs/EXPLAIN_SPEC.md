# EXPLAIN_SPEC — M8 explanations ("why is that the best move?")

Today a puzzle result says WHAT the best move was and its win-prob edge.
This spec adds WHY — grounded, never-hallucinated explanations built by
diffing exact position features, template-rendered (phase 1) and
optionally LLM-verbalized from those same facts (phase 2).

**The key liberating fact:** explanations need NO search. Every fact
below is computed from the stored position + a candidate move via cheap
exact functions that already exist (`agents/heuristic.py`:
`_resource_pips`, `placement_value`, `_port_type_at`;
`engine/rules.py`: `_legal_settlement_vertices`; `search/value.py`:
`_build_progress`). So the ~3,300 existing puzzles get explanations from
a minutes-long annotation script — no relabeling, no engine time.

## 1. Where explanations appear

1. **Puzzle result screen** (main target): one paragraph — why the best
   move is best, AND, when the user picked something else, one sentence
   on what their move missed (computed at submit time from the same
   feature functions; the state is already reconstructed there).
2. **Post-game review rows** (REVIEW_SPEC): same `facts → render`
   pipeline, one line per reviewed decision.

## 2. Architecture: facts, then words

New module `puzzles/explain.py` with a hard separation:

```
move_facts(state, action, actor) -> Facts     # exact, numeric, testable
render(facts_best, facts_alt, phase) -> str   # words from facts, nothing else
```

`Facts` (a dict; stored per puzzle for the best + runner-up so the UI and
a future LLM can re-render without recomputation):

- `prod_gain`: {resource: pips} added by the move (settlement/city:
  adjacent-hex pips; robber-adjusted)
- `prod_total_after` / `diversity_after`: per-player totals + count of
  nonzero resources
- `port_gained`: "3:1" | "2:1 wheat" | null; `ratio_improved`: {res: 4→2}
- `opens`: for roads — list of {vertex_notation, pips} the road makes
  placeable+connected next turn; for settlements — spots it takes FROM
  the opponent's current legal set (`denies_spot`: bool + notation)
- `robber`: for MOVE_ROBBER — {opp_pips_blocked: {res: pips},
  my_pips_blocked, steal_target_cards}
- `race`: {vp_after, lr_len_delta, lr_takes/holds: bool, knights_after,
  la_takes/holds: bool}
- `economy`: {build_progress_city, build_progress_settlement,
  hand_after, discard_exposed: hand_after > 9}
- `enables_now`: for trades/YoP — the build the new hand affords that it
  didn't before ("completes city cost"), from `COST_*` checks
- `dev_deck`: for BUY_DEV — {deck_left, expected_knights} (expected
  composition from the public 25-card mix minus publicly PLAYED cards —
  bought-but-unplayed stays unknown; use expectation, phrase as odds)

All notations reuse `trainer/actions.py` (`vertex_notation` etc.) so
explanation text matches what the UI calls things.

## 3. Rendering (phase 1: templates)

Selection logic, not prose logic: score each fact-diff between best and
alternative by salience (fixed order of importance per phase), emit the
top 1–2 as clauses. One lead sentence + at most two clauses; hard cap
~220 chars. Examples of the target register:

- placement: "9W·8S·5B adds 12 pips across three resources and a 2:1
  wheat port; the popular 10L·9W spot adds more wood but leaves you
  ore-blind."
- robber: "Robber to 8🌾 denies the opponent 5 pips of their strongest
  resource while your own production stays untouched; 6🌲 blocks only 3."
- road: "This road opens the 10W·9O spot — the last strong ore seat on
  the board; the engine's line settles it next turn."
- trade: "Trading 4 wood → 1 ore completes your city cost this turn —
  a full VP of tempo; holding the wood risks a discard at 8 cards."
- devcard: "Playing the knight now retakes Largest Army (2 ⚔ vs 2) and
  unblocks your 6🪨."
- endgame: "At 12 VP the race is arithmetic: the city is 2 VP in hand;
  every other line needs two more turns."

Rules that keep it honest:
- A clause may cite ONLY numbers present in the facts dicts.
- If no fact-diff clears its salience threshold, fall back to today's
  generic line (win-prob edge) — never pad with vague prose.
- Ties (several 100-point moves) get "equally strong:" phrasing listing
  the co-best moves.

## 4. Integration points

1. `puzzles/labeling.py::_explain` → replaced by
   `explain.render(move_facts(best), move_facts(second), phase)`; facts
   for the top-3 moves stored in a new puzzle field `facts` (schema
   bump: `Puzzle.facts: dict | None = None`; JSONL is forward-compatible
   — loader defaults it).
2. `scripts/annotate_explanations.py` — one pass over an existing JSONL:
   reconstruct each state, compute facts + rendered text for stored
   moves, rewrite atomically. Run on puzzles_v5; minutes.
3. `trainer/service.py::submit` — after scoring, if the user's move ≠
   best, compute `move_facts(chosen)` and append one "yours missed X"
   sentence to the explanation.
4. Review worker (when built) calls the same two functions per row.

## 5. Phase 2: LLM verbalization (optional, later)

Offline batch: for each puzzle, prompt = board notation + facts dicts +
the template sentence; ask for ≤2 sentences, cache into the puzzle field
`explanation_llm`. Guardrails: the prompt forbids numbers not present in
facts; a validator rejects outputs containing digits absent from the
facts; rejected → keep template. Model: claude-haiku-4-5 (cheap, this is
constrained summarization); ~3,300 puzzles ≈ ~2M tokens ≈ a few dollars.
UI prefers `explanation_llm` when present. This phase is pure polish —
ship phase 1 first and judge.

## 6. Tests (`tests/test_explain.py`)

- Golden facts: constructed positions with known arithmetic — settlement
  on 6/8/9 hexes yields exactly those pips; road opening exactly one
  known vertex; robber blocking a computed pip count; trade completing a
  city (COST check).
- Honesty property: for random mined candidates, every resource named in
  the rendered string has a nonzero diff in facts; every digit in the
  string appears in facts values.
- Fallback: a flat position (no salient diffs) renders the generic line.
- Round-trip: annotate script preserves puzzle count/ids and only adds
  fields; trainer serves annotated sets unchanged (`test_trainer` green).

## 7. Effort + order

`explain.py` ~250 lines; integrations ~60; annotate script ~40; tests
~120 — about a day, machine-independent, no relabeling required. Build
AFTER hosting/accounts (ROADMAP_V2 sequencing) unless puzzle feedback
demands it sooner; build BEFORE phase-2 LLM anything.
