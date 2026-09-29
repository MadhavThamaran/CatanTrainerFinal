"""Account + ratings storage (HOSTING.md step 1).

Two implementations behind one interface, selected by `DATABASE_URL`:
`JsonStore` (default, local dev — a single JSON file) and `PgStore`
(hosted, Neon Postgres). `Ratings` (elo.py) is the only other module that
touches a store, via `load_ratings`/`save_ratings`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol


class Store(Protocol):
    def get_user(self, name: str) -> dict | None: ...
    def create_user(self, name: str, pw_hash: str) -> dict: ...
    def load_ratings(self, user_id: int) -> dict: ...
    def save_ratings(self, user_id: int, data: dict) -> None: ...


class UsernameTaken(ValueError):
    pass


class JsonStore:
    """Local dev default: one JSON file, `{"users": {name: {id, pw_hash}},
    "ratings": {user_id: {...}}, "next_id": int}`."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        if self._path.exists():
            d = json.loads(self._path.read_text())
        else:
            d = {}
        if "users" in d:
            self._data = d
        elif "user_rating" in d:
            # Pre-accounts single-user format (no login): preserve it under
            # a reserved id rather than silently dropping real rating
            # history, even though it isn't attached to any account.
            self._data = {"users": {}, "ratings": {"0": d}, "next_id": 1}
            self._save()
        else:
            self._data = {"users": {}, "ratings": {}, "next_id": 1}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=1))

    def get_user(self, name: str) -> dict | None:
        u = self._data["users"].get(name)
        return {"id": u["id"], "name": name, "pw_hash": u["pw_hash"]} if u else None

    def create_user(self, name: str, pw_hash: str) -> dict:
        if name in self._data["users"]:
            raise UsernameTaken(name)
        uid = self._data["next_id"]
        self._data["next_id"] += 1
        self._data["users"][name] = {"id": uid, "pw_hash": pw_hash}
        self._save()
        return {"id": uid, "name": name, "pw_hash": pw_hash}

    def load_ratings(self, user_id: int) -> dict:
        return self._data["ratings"].get(str(user_id), {})

    def save_ratings(self, user_id: int, data: dict) -> None:
        self._data["ratings"][str(user_id)] = data
        self._save()


class PgStore:
    """Hosted: Neon/Postgres via psycopg. Creates its tables on connect."""

    def __init__(self, database_url: str):
        import psycopg

        self._psycopg = psycopg
        self._conn = psycopg.connect(database_url, autocommit=True)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                 id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                 pw_hash TEXT NOT NULL, created TIMESTAMPTZ DEFAULT now())"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS ratings (
                 user_id INT REFERENCES users(id), key TEXT, value JSONB,
                 PRIMARY KEY (user_id, key))"""
        )

    def get_user(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, name, pw_hash FROM users WHERE name = %s", (name,)
        ).fetchone()
        return {"id": row[0], "name": row[1], "pw_hash": row[2]} if row else None

    def create_user(self, name: str, pw_hash: str) -> dict:
        try:
            row = self._conn.execute(
                "INSERT INTO users (name, pw_hash) VALUES (%s, %s) RETURNING id",
                (name, pw_hash),
            ).fetchone()
        except self._psycopg.errors.UniqueViolation as exc:
            raise UsernameTaken(name) from exc
        return {"id": row[0], "name": name, "pw_hash": pw_hash}

    def load_ratings(self, user_id: int) -> dict:
        row = self._conn.execute(
            "SELECT value FROM ratings WHERE user_id = %s AND key = 'main'", (user_id,)
        ).fetchone()
        return row[0] if row else {}

    def save_ratings(self, user_id: int, data: dict) -> None:
        from psycopg.types.json import Json

        self._conn.execute(
            """INSERT INTO ratings (user_id, key, value) VALUES (%s, 'main', %s)
               ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value""",
            (user_id, Json(data)),
        )


def get_store(state_path: str | Path) -> Store:
    """DATABASE_URL set -> PgStore (hosted); otherwise JsonStore (local dev)."""
    url = os.environ.get("DATABASE_URL")
    return PgStore(url) if url else JsonStore(state_path)
