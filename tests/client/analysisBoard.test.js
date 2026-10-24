"use strict";
// Client-side regressions for the analysis board (ANALYSIS_SPEC), driven
// against the REAL trainer/static/index.html via jsdom — see
// tests/client/support/loadApp.js. Covers two bugs found by hand in a real
// session that the Python suite (tests/test_analysis.py) could not catch
// because they were purely in the client's rendering/click-handling logic.
const test = require("node:test");
const assert = require("node:assert/strict");

const { loadApp } = require("./support/loadApp");
const { waitFor } = require("./support/wait");
const { robberPuzzleRoot, robberPuzzleAfterMove } = require("./fixtures/analysisNodes");

function analysisRoutes({ evalLines = [] } = {}) {
  return {
    "/api/analysis/new": async () => ({ analysis_id: "an-test", root: robberPuzzleRoot() }),
    "/api/analysis/eval": async () => ({ lines: evalLines }),
    "/api/analysis/apply": async () => ({ node: robberPuzzleAfterMove() }),
  };
}

test("clicking an engine line applies the move directly, with no chance-forcing popup", async () => {
  // /api/analysis/eval rows are describe_move()'s dict (codec_id/kind/
  // target/label/category — see trainer/actions.py::_mk) plus q/visits
  // (trainer/analysis.py::eval_node). Derive from the same root move
  // fixture rather than hand-typing a shorter dict: a `category`-less
  // fixture here previously let a real bug slip past this exact test (the
  // old buggy code branches on `move.category`, so a fixture missing that
  // field can't exercise the branch it's meant to guard).
  const evalLines = [{ ...robberPuzzleRoot().moves[0], q: 0.41, visits: 50 }];
  const { window, document, fetchLog } = loadApp(analysisRoutes({ evalLines }));

  await window.openAnalysis("puzzle", "p1");
  await waitFor(() => document.querySelectorAll("#analysislinesbody .linerow").length > 0);

  // Precondition: this is exactly the scenario from the bug report — a
  // robber move with a non-empty revealed opponent hand, which is the
  // condition that used to route the click through the chance picker.
  assert.notEqual(document.getElementById("chancepicker").style.display, "flex");

  document.querySelector("#analysislinesbody .linerow").click();
  await waitFor(() => fetchLog.some((c) => c.path === "/api/analysis/apply"));

  // The fix: engine-line clicks go straight to analysisApply(line, null) —
  // never through analysisStage()'s chance-picker branch.
  assert.notEqual(document.getElementById("chancepicker").style.display, "flex");
  const applyCall = fetchLog.find((c) => c.path === "/api/analysis/apply");
  assert.equal(applyCall.body.codec_id, 101);
  assert.equal(applyCall.body.forced, null);

});

test("manually clicking the same robber move on the board still offers to force the steal", async () => {
  // Guards against "fixing" bug #1 by deleting the chance-forcing feature
  // outright instead of scoping it to manual board clicks (ANALYSIS_SPEC
  // §3's "choose the dice" superpower must still work from the board).
  // Top-level `let`/`const` in the page's classic script (PZ, ANALYSIS_*)
  // aren't properties of `window` per JS semantics, so tests can't read
  // them back — use the same move object the mocked root node serves.
  const move = robberPuzzleRoot().moves[0];
  assert.equal(move.category, "robber");
  const { window, document } = loadApp(analysisRoutes());

  await window.openAnalysis("puzzle", "p1");
  window.stage(move); // the board/action-bar entry point, not an engine-line click

  assert.equal(document.getElementById("chancepicker").style.display, "flex");
  assert.equal(document.getElementById("chancetitle").textContent, "Force the steal");
  const options = [...document.querySelectorAll("#chanceopts .chanceopt")].map(
    (o) => o.textContent
  );
  assert.ok(options.some((t) => t.includes("wood")));

});

test("the Random button in the chance picker resolves with real randomness (forced: null)", async () => {
  const move = robberPuzzleRoot().moves[0];
  const { window, document, fetchLog } = loadApp(analysisRoutes());

  await window.openAnalysis("puzzle", "p1");
  window.stage(move);
  assert.equal(document.getElementById("chancepicker").style.display, "flex");

  document.getElementById("chancerandom").click();
  await waitFor(() => fetchLog.some((c) => c.path === "/api/analysis/apply"));

  assert.equal(document.getElementById("chancepicker").style.display, "none");
  const applyCall = fetchLog.find((c) => c.path === "/api/analysis/apply");
  assert.equal(applyCall.body.forced, null);

});
