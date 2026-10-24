"use strict";
// Boots the REAL trainer/static/index.html inside jsdom: the exact shipped
// markup + inline <script>, not a reimplementation or an extracted copy. A
// test gets back the live `window`/`document` plus a `fetchLog` of every
// call the page's own code made, so assertions exercise the real client
// logic end to end with no risk of test/prod drift.
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const INDEX_HTML = path.join(__dirname, "..", "..", "..", "trainer", "static", "index.html");

// router: url -> (body, init) -> response-shaped plain object (or a Promise
// of one). Matched by exact pathname first, then by the longest routes[]
// prefix. Anything unmatched gets a generic empty-ok response so bootstrap
// fetches the test doesn't care about (e.g. /api/me) never throw.
function makeFetch(routes, fetchLog) {
  return async (url, init) => {
    const u = new URL(url, "http://localhost/");
    const body = init && init.body ? JSON.parse(init.body) : undefined;
    fetchLog.push({ path: u.pathname + u.search, method: (init && init.method) || "GET", body });
    const handler = routes[u.pathname] || routes[u.pathname + u.search];
    const payload = handler ? await handler(body, u) : {};
    return {
      ok: true,
      status: 200,
      json: async () => payload,
    };
  };
}

/**
 * @param {Object} routes - map of pathname (or pathname+search) -> async (body, url) => jsonPayload
 * @returns {{window, document, fetchLog}}
 */
function loadApp(routes = {}) {
  const html = fs.readFileSync(INDEX_HTML, "utf8");
  const fetchLog = [];
  const dom = new JSDOM(html, {
    url: "http://localhost:8321/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      // Must exist before the page's own inline <script> runs
      // checkAuthAndStart() at the bottom of the body.
      window.fetch = makeFetch(routes, fetchLog);
    },
  });
  return { window: dom.window, document: dom.window.document, fetchLog };
}

module.exports = { loadApp };
