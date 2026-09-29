"""Passwords + session cookies (HOSTING.md step 1): stdlib only, done
properly but minimally — no email verification, no password reset (a
game rating, not a bank)."""
from __future__ import annotations

import hashlib
import hmac
import os
import time

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SALT_LEN = 16
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days
MIN_PASSWORD_LEN = 8


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_LEN)
    return salt.hex() + ":" + _scrypt(password, salt).hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":")
    except ValueError:
        return False
    digest = _scrypt(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest.hex(), digest_hex)


def make_session(user_id: int, secret: str, ttl: int = SESSION_TTL) -> str:
    expiry = int(time.time()) + ttl
    payload = f"{user_id}:{expiry}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session(cookie: str, secret: str) -> int | None:
    try:
        uid_s, expiry_s, sig = cookie.split(":")
    except ValueError:
        return None
    payload = f"{uid_s}:{expiry_s}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if int(expiry_s) < time.time():
        return None
    return int(uid_s)


class RateLimiter:
    """Naive in-memory per-key counter (e.g. per IP) — good enough for v1
    signup/login throttling. Not shared across processes/instances."""

    def __init__(self, limit: int, window_seconds: float):
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self._window]
        if len(hits) >= self._limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
