"""HOSTING.md step 1: password hashing, session cookies, storage, and the
full signup/login/submit HTTP round trip (accounts layer)."""
from __future__ import annotations

import http.client
import json
import threading

import pytest

from test_trainer import _puzzle_file
from trainer import JsonStore, TrainerService
from trainer import auth
from trainer.server import make_handler
from trainer.store import UsernameTaken


# --- auth.py: passwords + session cookies ---


def test_password_hash_verify_roundtrip():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", h)
    assert not auth.verify_password("wrong password", h)
    # salted: two hashes of the same password differ
    assert auth.hash_password("same password") != auth.hash_password("same password")


def test_session_make_verify_roundtrip():
    token = auth.make_session(42, "s3cret")
    assert auth.verify_session(token, "s3cret") == 42
    assert auth.verify_session(token, "wrong-secret") is None
    assert auth.verify_session("garbage", "s3cret") is None


def test_session_expired_rejected():
    token = auth.make_session(1, "secret", ttl=-1)  # already expired
    assert auth.verify_session(token, "secret") is None


def test_session_tampered_rejected():
    token = auth.make_session(1, "secret")
    uid, expiry, sig = token.split(":")
    tampered = f"999:{expiry}:{sig}"
    assert auth.verify_session(tampered, "secret") is None


def test_rate_limiter():
    rl = auth.RateLimiter(limit=2, window_seconds=60)
    assert rl.allow("1.2.3.4")
    assert rl.allow("1.2.3.4")
    assert not rl.allow("1.2.3.4")     # 3rd hit within the window: blocked
    assert rl.allow("5.6.7.8")          # different key: independent budget


# --- store.py: JsonStore ---


def test_jsonstore_users_and_ratings_roundtrip(tmp_path):
    store = JsonStore(tmp_path / "s.json")
    assert store.get_user("alice") is None
    u = store.create_user("alice", "hash1")
    assert store.get_user("alice") == u
    with pytest.raises(UsernameTaken):
        store.create_user("alice", "hash2")

    assert store.load_ratings(u["id"]) == {}
    store.save_ratings(u["id"], {"user_rating": 1600.0})
    # fresh instance from disk sees the same data
    store2 = JsonStore(tmp_path / "s.json")
    assert store2.get_user("alice") == u
    assert store2.load_ratings(u["id"]) == {"user_rating": 1600.0}


# --- full HTTP round trip: signup, login, logout, submit ---


@pytest.fixture
def live_server(tmp_path):
    path, puzzle = _puzzle_file(tmp_path)
    store = JsonStore(tmp_path / "state.json")
    service = TrainerService(path, store)
    handler = make_handler(service, play=None, review=None, session_secret="test-secret",
                            secure_cookies=False)
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, puzzle
    finally:
        server.shutdown()
        t.join(timeout=5)


def _request(port, method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"sid={cookie}"
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    cookie_out = None
    set_cookie = resp.getheader("Set-Cookie")
    if set_cookie and "sid=" in set_cookie:
        cookie_out = set_cookie.split("sid=", 1)[1].split(";", 1)[0]
    conn.close()
    return resp.status, data, cookie_out


def test_signup_login_logout_roundtrip(live_server):
    port, _ = live_server
    status, data, cookie = _request(
        port, "POST", "/api/signup", {"name": "alice", "password": "hunter2pass"}
    )
    assert status == 200 and data["ok"] and cookie

    # duplicate signup rejected
    status, data, _ = _request(
        port, "POST", "/api/signup", {"name": "alice", "password": "otherpass1"}
    )
    assert status == 409

    # wrong password rejected
    status, data, _ = _request(
        port, "POST", "/api/login", {"name": "alice", "password": "wrongpass1"}
    )
    assert status == 401

    # correct login round-trips a working session
    status, data, cookie2 = _request(
        port, "POST", "/api/login", {"name": "alice", "password": "hunter2pass"}
    )
    assert status == 200 and cookie2

    # logout clears the cookie session-side is a no-op here (stateless
    # cookies); the client just drops it -- verify the endpoint responds ok
    status, data, _ = _request(port, "POST", "/api/logout", cookie=cookie2)
    assert status == 200 and data["ok"]


def test_unauthenticated_requests_rejected(live_server):
    port, _ = live_server
    status, data, _ = _request(port, "GET", "/api/next")
    assert status == 401
    status, data, _ = _request(
        port, "POST", "/api/submit", {"puzzle_id": "x", "codec_id": 0}
    )
    assert status == 401


def test_two_users_get_independent_ratings(live_server):
    port, puzzle = live_server
    _, _, cookie_a = _request(
        port, "POST", "/api/signup", {"name": "alice", "password": "hunter2pass"}
    )
    _, _, cookie_b = _request(
        port, "POST", "/api/signup", {"name": "bob", "password": "hunter2pass"}
    )

    status, next_a, _ = _request(port, "GET", "/api/next", cookie=cookie_a)
    assert status == 200
    rating_a_before = next_a["user_rating"]

    status, sub, _ = _request(
        port, "POST", "/api/submit",
        {"puzzle_id": puzzle.id, "codec_id": puzzle.best_codec_id},
        cookie=cookie_a,
    )
    assert status == 200 and sub["rating"]["rated"]

    # bob's rating is untouched by alice's submission
    status, next_b, _ = _request(port, "GET", "/api/next", cookie=cookie_b)
    assert status == 200
    assert next_b["user_rating"] == rating_a_before  # both start at 1500

    status, data, _ = _request(
        port, "POST", "/api/submit",
        {"puzzle_id": puzzle.id, "codec_id": puzzle.best_codec_id},
        cookie=cookie_b,
    )
    assert status == 200 and data["rating"]["rated"]  # bob's first attempt, independently rated
