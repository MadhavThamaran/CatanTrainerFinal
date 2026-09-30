"""The tactics trainer web app (M7) — stdlib HTTP server, zero engine work
at request time beyond reconstructing the stored position.

Usage:
  uv run python -m trainer.server [--puzzles data/puzzles_v5.jsonl]
                                  [--state data/trainer_state.json]
                                  [--port 8321]
then open http://localhost:8321

Accounts (HOSTING.md step 1): set DATABASE_URL to use hosted Postgres
instead of the local JSON file, and SESSION_SECRET to make login sessions
survive a restart (a random one is generated otherwise — fine for local
dev, but every restart logs everyone out).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import auth
from .service import TrainerService
from .store import UsernameTaken, get_store

_STATIC = Path(__file__).parent / "static"
_SIGNUP_LIMIT = auth.RateLimiter(limit=20, window_seconds=3600)
_LOGIN_LIMIT = auth.RateLimiter(limit=20, window_seconds=3600)


def _parse_cookie(header: str | None, name: str) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def make_handler(
    service: TrainerService,
    play,
    review,
    session_secret: str,
    secure_cookies: bool,
    lab=None,
    lab_stats_for=None,
):
    def _user_id(handler: BaseHTTPRequestHandler) -> int | None:
        cookie = _parse_cookie(handler.headers.get("Cookie"), "sid")
        return auth.verify_session(cookie, session_secret) if cookie else None

    def _set_session_cookie(handler: BaseHTTPRequestHandler, user_id: int) -> None:
        token = auth.make_session(user_id, session_secret)
        attrs = "HttpOnly; Path=/; SameSite=Lax; Max-Age=" + str(auth.SESSION_TTL)
        if secure_cookies:
            attrs += "; Secure"
        handler.send_header("Set-Cookie", f"sid={token}; {attrs}")

    def _clear_session_cookie(handler: BaseHTTPRequestHandler) -> None:
        handler.send_header("Set-Cookie", "sid=; HttpOnly; Path=/; Max-Age=0")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _json(self, obj, status=200, extra_headers=()):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for fn in extra_headers:
                fn(self)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            try:
                if path in ("/", "/index.html"):
                    body = (_STATIC / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/me":
                    uid = _user_id(self)
                    self._json({"user_id": uid} if uid is not None else {"user_id": None})
                elif path == "/api/next":
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    phase = parse_qs(query).get("phase", [None])[0]
                    self._json(service.next_puzzle(uid, phase=phase))
                elif path == "/api/dashboard":
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    self._json(service.dashboard(uid))
                elif path == "/api/srs/summary":
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    self._json(service.srs_summary(uid))
                elif path == "/api/srs/next":
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    result = service.next_srs(uid)
                    if result is None:
                        self._json({"error": "queue is empty"}, 404)
                        return
                    self._json(result)
                elif path == "/api/ladder":
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    if play is None:
                        self._json({"error": "play mode not available on this deployment"}, 404)
                        return
                    self._json(service.ladder_view(uid))
                elif path == "/api/play/new":
                    if play is None:
                        self._json({"error": "play mode not available on this deployment"}, 404)
                        return
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    qs = parse_qs(query)
                    coach = qs.get("coach", ["0"])[0] in ("1", "true")
                    rated = qs.get("rated", ["0"])[0] in ("1", "true")
                    rung_str = qs.get("rung", [None])[0]
                    rung = int(rung_str) if rung_str is not None else None
                    result = play.new_game(
                        user_id=uid, rung=rung, rated=rated, coach=coach,
                    )
                    self._json(result, 400 if "error" in result else 200)
                elif path == "/api/review/poll":
                    if review is None:
                        self._json({"error": "review not available on this deployment"}, 404)
                        return
                    qs = parse_qs(query)
                    result = review.poll(
                        qs.get("id", [""])[0], int(qs.get("from", ["0"])[0])
                    )
                    self._json(result, 404 if "error" in result else 200)
                elif path == "/api/lab/new":
                    if lab is None:
                        self._json({"error": "placement lab not available on this deployment"}, 404)
                        return
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    self._json(lab.new_drill(user_id=uid))
                elif path == "/api/lab/stats":
                    if lab is None:
                        self._json({"error": "placement lab not available on this deployment"}, 404)
                        return
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    self._json(lab_stats_for(uid), 200)
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": repr(exc)}, 500)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            try:
                req = json.loads(self.rfile.read(length)) if length else {}
                if self.path == "/api/signup":
                    self._signup(req)
                elif self.path == "/api/login":
                    self._login(req)
                elif self.path == "/api/logout":
                    self._json({"ok": True}, extra_headers=[_clear_session_cookie])
                elif self.path == "/api/submit":
                    uid = _user_id(self)
                    if uid is None:
                        self._json({"error": "unauthenticated"}, 401)
                        return
                    road = req.get("road_codec_id")
                    road = int(road) if road is not None else None
                    submit_fn = service.submit_srs if req.get("srs") else service.submit
                    result = submit_fn(req["puzzle_id"], int(req["codec_id"]), uid, road)
                    self._json(result, 404 if result.get("error") else 200)
                elif self.path == "/api/play/act":
                    if play is None:
                        self._json({"error": "play mode not available on this deployment"}, 404)
                        return
                    result = play.act(
                        req["session"],
                        codec_id=req.get("codec_id"),
                        discard=req.get("discard"),
                        confirm=bool(req.get("confirm")),
                        coach_set=req.get("coach_set"),
                    )
                    self._json(result)
                elif self.path == "/api/review/start":
                    if review is None:
                        self._json({"error": "review not available on this deployment"}, 404)
                        return
                    result = review.start(req["game_id"])
                    self._json(result, result.pop("status", 404) if "error" in result else 200)
                elif self.path == "/api/lab/act":
                    if lab is None:
                        self._json({"error": "placement lab not available on this deployment"}, 404)
                        return
                    result = lab.act(req["drill"], int(req["codec_id"]))
                    self._json(result, 404 if "error" in result else 200)
                else:
                    self._json({"error": "not found"}, 404)
            except KeyError as exc:
                self._json({"error": f"bad request: {exc}"}, 400)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": repr(exc)}, 500)

        def _signup(self, req: dict) -> None:
            if not _SIGNUP_LIMIT.allow(self.client_address[0]):
                self._json({"error": "too many signups, try again later"}, 429)
                return
            name, password = req.get("name", ""), req.get("password", "")
            if not (3 <= len(name) <= 32) or not name.isalnum():
                self._json({"error": "username must be 3-32 alphanumeric characters"}, 400)
                return
            if len(password) < auth.MIN_PASSWORD_LEN:
                self._json({"error": f"password must be >= {auth.MIN_PASSWORD_LEN} characters"}, 400)
                return
            if service.store.get_user(name) is not None:
                self._json({"error": "username taken"}, 409)
                return
            try:
                user = service.store.create_user(name, auth.hash_password(password))
            except UsernameTaken:
                self._json({"error": "username taken"}, 409)
                return
            self._json(
                {"ok": True, "name": name},
                extra_headers=[lambda h: _set_session_cookie(h, user["id"])],
            )

        def _login(self, req: dict) -> None:
            if not _LOGIN_LIMIT.allow(self.client_address[0]):
                self._json({"error": "too many attempts, try again later"}, 429)
                return
            name, password = req.get("name", ""), req.get("password", "")
            user = service.store.get_user(name)
            if user is None or not auth.verify_password(password, user["pw_hash"]):
                self._json({"error": "invalid username or password"}, 401)
                return
            self._json(
                {"ok": True, "name": name},
                extra_headers=[lambda h: _set_session_cookie(h, user["id"])],
            )

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--puzzles", default="data/puzzles_v5.jsonl")
    ap.add_argument("--state", default="data/trainer_state.json",
                     help="JsonStore path (ignored if DATABASE_URL is set)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--default-rung", type=int, default=6,
                     help="ladder rung (1-8) a game starts on when the client omits ?rung=")
    ap.add_argument("--no-play", action="store_true",
                     help="disable play-vs-bot (no torch import) — for hosted deploys")
    ap.add_argument("--review-sims", type=int, default=512,
                     help="post-game review search depth (deeper than play)")
    ap.add_argument("--review-dets", type=int, default=6)
    ap.add_argument("--coach-sims", type=int, default=160,
                     help="coach-mode search budget (judges at a fixed reference net)")
    ap.add_argument("--coach-dets", type=int, default=4)
    ap.add_argument("--lab-sims", type=int, default=256,
                     help="placement lab grading budget (single-seed, coarse-but-fast)")
    ap.add_argument("--lab-dets", type=int, default=4)
    args = ap.parse_args()

    store = get_store(args.state)
    service = TrainerService(args.puzzles, store)

    play, review, lab, lab_stats_for = None, None, None, None
    if not args.no_play:
        from .play import PlayService
        from .review import ReviewService
        from .lab import LabService, stats_for as lab_stats_for

        play = PlayService(
            ratings_for=service.ratings_for, default_rung=args.default_rung,
            coach_sims=args.coach_sims, coach_dets=args.coach_dets,
        )
        review = ReviewService(sims=args.review_sims, dets=args.review_dets)
        lab = LabService(sims=args.lab_sims, dets=args.lab_dets)

    session_secret = os.environ.get("SESSION_SECRET")
    if not session_secret:
        session_secret = secrets.token_hex(32)
        print("warning: SESSION_SECRET not set — using a random secret for this "
              "process; sessions will not survive a restart")
    secure_cookies = bool(os.environ.get("RENDER"))

    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(
            service, play, review, session_secret, secure_cookies,
            lab=lab, lab_stats_for=lab_stats_for,
        ),
    )
    print(f"catan tactics trainer: {len(service.puzzles)} puzzles loaded")
    print(f"play mode: bot ladder (default rung {args.default_rung})" if play else "play mode: disabled")
    print(f"open http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
