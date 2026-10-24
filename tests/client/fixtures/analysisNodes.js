"use strict";
// Minimal but shape-correct fixtures for AnalysisSession.node_view()
// (trainer/analysis.py) and describe_move() (trainer/actions.py). Only the
// fields the exercised render paths actually read are filled in — see
// renderBoard/renderSide/renderRevealed in trainer/static/index.html.

const LAYOUT = {
  width: 100,
  height: 100,
  center: { x: 50, y: 50 },
  hexes: [{ id: 0, x: 50, y: 50, vertex_ids: [0, 1, 2, 3, 4, 5] }],
  vertices: [
    { id: 0, x: 10, y: 10 },
    { id: 1, x: 90, y: 10 },
    { id: 2, x: 90, y: 90 },
    { id: 3, x: 50, y: 100 },
    { id: 4, x: 10, y: 90 },
    { id: 5, x: 10, y: 50 },
  ],
  edges: { 0: { v1: 0, v2: 1 }, 1: { v1: 1, v2: 2 } },
};

function board({ buildings = {}, roads = {}, robber = false } = {}) {
  return {
    hexes: [{ id: 0, terrain: "hills", number: 9, robber }],
    ports: [],
    buildings,
    roads,
  };
}

function context(overrides = {}) {
  return {
    resources: { wood: 0, brick: 0, sheep: 0, wheat: 0, ore: 0 },
    dev_cards: {},
    my_vp: 2,
    opp_visible_vp: 2,
    opp_hand_size: 5,
    opp_dev_count: 0,
    my_knights: 0,
    opp_knights: 0,
    longest_road: null,
    largest_army: null,
    deck_count: 25,
    last_roll: null,
    setup: true,
    ...overrides,
  };
}

function revealed(overrides = {}) {
  return {
    opp_hand: { wood: 4, sheep: 1 },
    opp_devs: {},
    deck_count: 25,
    deck: {},
    ...overrides,
  };
}

// The puzzle from the bug report: a robber-placement decision with a
// non-empty revealed opponent hand (the condition that routes a "robber"
// category move through the chance-forcing picker in analysisStage()).
function robberPuzzleRoot() {
  return {
    analysis_id: "an-test",
    node: "root",
    parent: null,
    game_over: false,
    winner: null,
    layout: LAYOUT,
    board: board(),
    context: context(),
    revealed: revealed(),
    dice_probs: null,
    moves: [
      { codec_id: 101, kind: "hex", target: 0, label: "Robber to 9O", category: "robber" },
    ],
  };
}

function robberPuzzleAfterMove() {
  return {
    analysis_id: "an-test",
    node: "n1",
    parent: "root",
    game_over: false,
    winner: null,
    layout: LAYOUT,
    board: board({ robber: true }),
    context: context(),
    revealed: revealed(),
    dice_probs: null,
    moves: [],
  };
}

module.exports = { LAYOUT, board, context, revealed, robberPuzzleRoot, robberPuzzleAfterMove };
