"use strict";
// Client-side regression for renderBoard()'s "mine" -> color mapping
// (trainer/static/index.html). This is the client half of the viewer-
// perspective bug (trainer/analysis.py, see tests/test_analysis.py's
// test_board_perspective_does_not_flip_when_the_turn_passes): the server
// now keeps "mine" pinned correctly, but nothing was guarding the
// RENDERER's own fill = b.mine ? YOU : OPP logic, or that moving to a new
// node doesn't leave a stale previous-node element behind in the SVG.
const test = require("node:test");
const assert = require("node:assert/strict");

const { loadApp } = require("./support/loadApp");
const { LAYOUT, board, context, revealed } = require("./fixtures/analysisNodes");

const YOU = "#2563eb";
const OPP = "#e0442d";

function node(id, parent, { buildings, roads, moves = [] }) {
  return {
    analysis_id: "an-test",
    node: id,
    parent,
    game_over: false,
    winner: null,
    layout: LAYOUT,
    board: board({ buildings, roads }),
    context: context(),
    revealed: revealed(),
    dice_probs: null,
    moves,
  };
}

test("a building/road render blue when mine, red when not, straight from the node payload", async () => {
  const root = node("root", null, {
    buildings: { 0: { mine: true, kind: "settlement" } },
    roads: { 0: { mine: true } },
    moves: [{ codec_id: 200, kind: "edge", target: 1, label: "Build road", category: "build_road" }],
  });
  const { window, document } = loadApp({
    "/api/analysis/new": async () => ({ analysis_id: "an-test", root }),
    "/api/analysis/eval": async () => ({ lines: [] }),
  });

  await window.openAnalysis("puzzle", "p1");
  const svg = document.getElementById("board");

  assert.equal(svg.querySelectorAll(`path[fill="${YOU}"]`).length, 1);
  assert.equal(svg.querySelectorAll(`path[fill="${OPP}"]`).length, 0);
  assert.equal(svg.querySelectorAll(`line[stroke="${YOU}"]`).length, 1);
});

test("switching to a node that drops a building/road clears it from the SVG (no stale elements)", async () => {
  const root = node("root", null, {
    buildings: { 0: { mine: true, kind: "settlement" } },
    roads: { 0: { mine: true } },
    moves: [{ codec_id: 200, kind: "edge", target: 1, label: "Build road", category: "build_road" }],
  });
  // The next node: a DIFFERENT building (not mine) and no road at all —
  // simulating a turn/perspective change plus a dropped road, the two
  // things a naive incremental render (instead of redraw-from-scratch)
  // could get wrong.
  const next = node("n1", "root", {
    buildings: { 0: { mine: false, kind: "settlement" } },
    roads: {},
  });
  const { window, document } = loadApp({
    "/api/analysis/new": async () => ({ analysis_id: "an-test", root }),
    "/api/analysis/eval": async () => ({ lines: [] }),
    "/api/analysis/apply": async () => ({ node: next }),
  });

  await window.openAnalysis("puzzle", "p1");
  window.stage(root.moves[0]); // non-chance category: applies straight through

  // analysisApply's fetch is async; its only observable effect once it
  // resolves is the re-render, so poll the SVG rather than the fetch log.
  const svg = document.getElementById("board");
  const start = Date.now();
  while (svg.querySelectorAll(`path[fill="${YOU}"]`).length !== 0) {
    if (Date.now() - start > 1000) throw new Error("timed out waiting for re-render");
    await new Promise((r) => setTimeout(r, 5));
  }

  assert.equal(svg.querySelectorAll(`path[fill="${YOU}"]`).length, 0);
  assert.equal(svg.querySelectorAll(`path[fill="${OPP}"]`).length, 1);
  assert.equal(svg.querySelectorAll("line").length, 0, "the dropped road must not linger");
});
