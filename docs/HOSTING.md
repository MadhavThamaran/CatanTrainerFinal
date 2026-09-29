# HOSTING.md — accounts + public deployment of the puzzle trainer

Goal: the puzzle trainer at a public HTTPS link where anyone can create an
account and build a persistent rating. Written to be executed step-by-step
(by you + a coding model); decisions are already made — follow them unless
something has genuinely changed.

**Status (2026-09-28): Step 1 (accounts layer) is done** — `trainer/store.py`
(`JsonStore`/`PgStore`), `trainer/auth.py` (scrypt + signed cookies),
`Ratings`/`TrainerService` parameterized per-user, signup/login/logout
endpoints, login card in the UI, `--host`/`--no-play` flags, torch moved
to the `play` optional-extra. Tests: `tests/test_hosting.py` (9 tests) +
updated `tests/test_trainer.py`. **Steps 2–3 (Neon + Render signup/deploy)
are manual browser steps for a human** — not yet done.

## Architecture decisions (made, with reasons)

- **Deploy the TRAINER only.** At serve time it is pure lightweight Python
  (positions and point tables are precomputed). **Play-vs-bot stays local
  for now**: each bot move runs ~1s of torch+MCTS (~500 MB RAM) — beyond
  free tiers. Revisit on a ~$5/mo VM later (section: Play mode later).
- **Platform: Render free tier** (long-running Python web service, HTTPS
  and a `*.onrender.com` URL included). Vercel is the wrong shape (no
  persistent processes, serverless-JS-first). Caveats accepted: the free
  instance sleeps after ~15 min idle (first hit takes ~30s), and its disk
  is EPHEMERAL — which forces the next decision.
- **Accounts + ratings in hosted Postgres (Neon free tier)**, not the
  local JSON file. Neon is a real free tier (0.5 GB), and the data is a
  handful of tiny tables.
- **Auth: usernames + passwords done properly but minimally** — stdlib
  `hashlib.scrypt` for hashing, signed session cookies (`hmac` over
  `user_id:expiry` with a server secret), no email verification, no
  password reset in v1 (it's a game rating, not a bank).

## Step 1 — accounts layer (code, ~half a day)

New module `trainer/auth.py` + a storage seam:

1. **Storage interface** `trainer/store.py`:
   - `class Store`: `get_user(name)`, `create_user(name, pw_hash)`,
     `load_ratings(user_id) -> dict`, `save_ratings(user_id, dict)`.
   - Two implementations: `JsonStore` (current behavior, default for local
     dev — keeps `data/trainer_state.json` working) and `PgStore`
     (psycopg, table DDL below). Select by env var `DATABASE_URL`.
2. **Schema** (PgStore creates on boot):
   ```sql
   CREATE TABLE IF NOT EXISTS users (
     id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL,
     pw_hash TEXT NOT NULL, created TIMESTAMPTZ DEFAULT now());
   CREATE TABLE IF NOT EXISTS ratings (
     user_id INT REFERENCES users(id), key TEXT, value JSONB,
     PRIMARY KEY (user_id, key));
   ```
   `ratings` holds the same dict `Ratings` serializes today (user Elo +
   per-puzzle entries + attempt log), keyed per user.
3. **`trainer/elo.py`**: parameterize `Ratings` by user — it currently
   owns one global pool backed by one file. Constructor takes a
   load/save callable pair from `Store` instead of a path.
4. **Endpoints** in `server.py`: `POST /api/signup {name, password}`,
   `POST /api/login`, `POST /api/logout`; session cookie `sid`
   (HttpOnly, Secure, SameSite=Lax). `/api/next` and `/api/submit`
   resolve the user from the cookie; unauthenticated → 401 and the UI
   shows the login card.
5. **UI**: a small login/signup card that replaces the sidebar until
   authenticated; show username + rating in the "You" row; logout link.
6. **Rate limiting**: naive in-memory counter per IP on signup/login
   (e.g. 20/hour) — enough for v1.
7. **Tests**: signup/login/logout round-trip; two users get independent
   ratings; wrong password rejected; unauthenticated submit rejected.
   Run everything against JsonStore; PgStore gets a smoke test if a
   `DATABASE_URL` is present.

Acceptance: local run with no env vars behaves exactly like today except
for the login card; with `DATABASE_URL` set, state lands in Postgres.

## Step 2 — Neon (5 minutes, browser)

neon.tech → sign up (GitHub login) → create project `catan-trainer` →
copy the connection string (looks like
`postgres://user:pw@ep-xxx.neon.tech/neondb?sslmode=require`).

## Step 3 — Render (15 minutes, browser)

1. render.com → sign up with the personal GitHub → New → Web Service →
   pick the `catan-trainer` repo.
2. Runtime: Python. Build command: `pip install uv && uv sync --frozen --no-dev`
   (`--no-dev` is load-bearing — the `dev` group carries torch/numpy/
   playwright/pytest for local development; skipping it plus passing
   `--no-play` at start keeps the Render build to psycopg + stdlib).
   Start command:
   `uv run python -m trainer.server --port $PORT --host 0.0.0.0 --no-play`
   (`--host`/`--no-play` are real flags on `server.py` now — done).
3. Environment: `DATABASE_URL` = the Neon string;
   `SESSION_SECRET` = output of `python -c "import secrets;print(secrets.token_hex(32))"`.
4. Instance type: Free. Deploy. The service appears at
   `https://<name>.onrender.com` — that's the shareable link.
5. Check: signup, solve a puzzle, redeploy, log in again — rating
   survived (it's in Neon, not on the instance).

Dependency note (done): torch lives in `[project.optional-dependencies]
play` (`uv sync --extra play` to add it back for the paid play-mode VM,
`docs/HOSTING.md`'s "Play mode later" section) rather than in the base
install, so the slim Render build never downloads it.

## Play mode later (optional, ~$5/mo)

A Hetzner CX22 / Fly.io shared-1x-1GB machine runs the full server
(torch CPU) fine for a handful of concurrent games. Same repo; build with
`uv sync --frozen --no-dev --extra play` (pulls the CPU-only torch wheel
via the `play` extra) and start without `--no-play`, checkpoint shipped in
the repo. Not free; defer until the puzzle trainer has users who ask for it.

## Costs summary
GitHub free, Neon free (0.5 GB ≫ needed), Render free (with idle sleep).
Total: $0 until play mode goes public.
