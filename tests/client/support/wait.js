"use strict";
// Polls a predicate until it's truthy. The app's own async fetch chains
// (e.g. applyAnalysisNode's un-awaited refreshAnalysisLines()) resolve on
// later microtask/timer ticks than the call that triggered them, so tests
// wait on an observable DOM/state condition instead of guessing a delay.
async function waitFor(predicate, { timeout = 1000, interval = 5 } = {}) {
  const start = Date.now();
  while (true) {
    const v = predicate();
    if (v) return v;
    if (Date.now() - start > timeout) throw new Error("waitFor: timed out");
    await new Promise((r) => setTimeout(r, interval));
  }
}

module.exports = { waitFor };
