# RUST_PORT_SPEC — the engine hot-loop port (contract & validation)

`apply_action`/`legal_actions` in Rust behind the existing Python
interface. This is ROADMAP A2, the compounding investment: measured,
the Python engine costs ~21 µs per apply_action and ~100 µs per wide
legal_actions call; it is ~10–15% of net-guided search but ~100% of
rollouts, mining games, and the heuristic side of everything. A 20–50×
hot loop makes every future generation, gate, labeling run, and lab
grade cheaper — forever.

This document is a CONTRACT, not a tutorial: what must be identical,
how identity is proven, and what is explicitly out of scope. The port
fails if any contract clause fails, regardless of how fast it is.

## 1. Scope

**Ported to Rust (crate `catan-engine-rs`, PyO3/maturin bindings):**
- `engine/types.py` constants (costs, thresholds, piece limits)
- `engine/topology.py` — as build-time-generated static tables (§6)
- `engine/state.py` GameState (owned by Rust; Python holds a handle)
- `engine/dice.py` — IID, **Balanced** (all ambiguity resolutions in
  PLAN §6 preserved), Scripted
- `engine/longest_road.py` — identical tie-breaking and holder retention
- `engine/rules.py` — `legal_actions`, `apply_action`, all helpers
- The RNG (§4) — the hardest and most important clause

**Stays in Python (calls into Rust):**
- `engine/board_gen.py` (cold: once per game; the generated board is
  passed into Rust state construction)
- `net/` entirely; `search/` tree logic initially (§8); `puzzles/`,
  `trainer/`, codec, encoder.
- The Python engine itself — RETAINED FOREVER as the reference oracle.

## 2. The frozen interface

The Rust-backed state must be a drop-in for every access the codebase
actually makes. The authoritative list (verify by grep before starting;
freeze `engine/` to bugfixes-only during the port):

- module functions: `new_game(seed)` (Python board_gen + Rust state),
  `legal_actions(state) -> list[Action]`, `apply_action(state, action)`
- state: `clone()`, `to_dict()/from_dict()`, `player_to_act()`,
  `observation(viewer)`, `total_vp(p)`, `visible_vp(p)`,
  `trade_ratio(p, res)`, and read attributes: `phase`, `winner`,
  `robber_hex`, `last_roll`, `free_roads`, `pending_robber`,
  `dev_played_this_turn`, `needs_roll`, `turn_count`, `dev_deck` (len),
  `longest_road_holder`, `largest_army_holder`, `buildings` (dict
  v -> (owner, Building)), `roads` (dict e -> owner), `players[p]`
  (`resources`, `dev_cards`, `hand_size()`, `knights_played`,
  `dev_bought_this_turn`, `roads_left`, `settlements_left`,
  `cities_left`)
- `Action` stays the Python frozen dataclass at the boundary;
  conversion to/from the internal Rust enum happens in the bindings.

**Ordering is part of the interface.** `legal_actions` list order is
consumed by `rng.choice(...)` in selfplay/tests and by canonical-order
sorts downstream — Rust must return actions in the EXACT order Python
does, per action type and within types. Same for dict iteration order
on `buildings`/`roads` (Python dicts are insertion-ordered; the
bindings must reconstruct that order or expose sorted views and change
zero observable behavior — prove via §5 Tier 2).

## 3. Non-goals (explicit)

- No rules changes, no new features, no "while we're in here" fixes —
  bug-for-bug compatibility. If the port reveals a Python engine bug,
  file it, replicate it, fix BOTH after the port lands.
- No MCTS/encoder port in this phase (§8).
- No performance work in the Python engine.

## 4. RNG parity (the crux)

All chance — dice draws, steal selection, dev-deck shuffle — flows from
`random.Random` (MT19937) seeded per game. The determinism contract
"(seed, action log) → identical game" and the entire differential-test
strategy require Rust to be **bit-exact** with CPython's:
- MT19937 core (portable; ~150 lines or the `rand_mt` crate)
- `getrandbits`-based `randrange` (CPython's rejection algorithm, not a
  modulo)
- `shuffle` (Fisher–Yates with CPython's exact index sequence)
- `choice` (index via `randrange(len)`)
- and the exact CALL ORDER the Python engine makes them in.

Acceptance for this clause alone: a harness seeds both RNGs identically
and compares 1e6 consecutive outputs of each primitive; then §5 tiers
prove the call-order coupling. The engine is integer-only (no float
state), which is what makes bit-exactness achievable — keep it so.

## 5. Validation tiers (all mandatory, in order)

1. **The pytest suite is the porting contract**: a conftest switch
   (`CATAN_ENGINE=rust`) routes `engine` imports to the Rust backend;
   all 117 tests must pass UNMODIFIED. Any test edit to make the port
   pass is a contract violation.
2. **Differential fuzz**: 10,000 seeded random games driven lockstep —
   after every single action, compare `legal_actions` (full ordered
   list) and a canonical state digest (stable serialization of
   `to_dict`, hashed; implemented identically both sides). First
   divergence dumps both states + the action log. Target: zero
   divergences; every divergence is a Rust bug until proven a Python
   bug (then replicate it).
3. **Replay corpus**: every recorded game in `data/games/*.json`
   replays on Rust to the identical final VP/winner (extends the
   existing replay-integrity test).
4. **Benchmark gates** (measured on the same machine, criterion +
   pytest-bench harness):
   - `apply_action` ≥ 20× Python (≤ ~1 µs vs ~21 µs)
   - full random game (the selfplay smoke pattern) ≥ 15×
   - end-to-end: heuristic-prior mining game ≥ 5× wall (Python search
     overhead dilutes the gain; record the number, don't chase it)
   Failing a gate = investigate, not ship-anyway.

## 6. Topology generation

Never hand-write the 19/54/72 tables. A Python script
(`scripts/gen_topology_rs.py`) dumps `TOPOLOGY`'s arrays
(vertex_neighbors, vertex_hexes, vertex_edges, edge_vertices,
edge_hexes, hex_vertices, coast/port slots) into a generated
`topology_gen.rs`, with a test asserting the generated tables equal the
Python ones element-for-element. Regenerate on any topology change
(there should be none).

## 7. Packaging & rollout

- `rust/` subdirectory crate; `maturin` builds a wheel; the wheel is a
  **soft dependency** — `engine/__init__.py` selects backend:
  `CATAN_ENGINE=python|rust` env var, default `python` until Tier 1–3
  are green in CI, then flip the default and keep the escape hatch.
- CI (or the local equivalent): run the suite BOTH ways on every
  change; run a 500-game fuzz nightly-equivalent.
- The differential harness (`scripts/engine_diff.py`) stays in the repo
  permanently — it's the regression tripwire for both engines.

## 8. Phase 2 candidates (separate specs when justified)

Rollouts fully inside Rust (no per-step FFI), `net/encode.py` as a Rust
function (it's 15–25% of search time post-lockstep), and ultimately the
MCTS descend/backprop loop. Each multiplies the previous; none is
justified until the Phase-1 port is proven and self-play is again the
measured bottleneck.

## 9. Effort & risks

Rules engine ~2–2.5k lines of Rust + ~400 bindings + ~150 RNG + ~200
harness Python: **1–2 focused weeks** for a capable coding model with
the suite as guardrails. Top risks, mitigations inline:
- RNG call-order drift (freeze `engine/` during the port; Tier 2
  catches instantly)
- ordering-contract violations (§2; Tier 2 compares ordered lists, not
  sets)
- silent behavioral "fixes" (§3 non-goal; Tier 1 unmodified-tests rule)
- platform float creep (keep the engine integer-only; there is no
  legitimate reason for an f64 in the rules path).
