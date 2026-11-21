"""Document storage for the bot: in memory (tests, demo) and PostgreSQL (production).

Document = (kind, company_id, key) -> JSON. Every request is scoped to company_id;
global search (by code hash, by Telegram id) goes only through find(), which returns the company_id
of the match, and from there the code works inside that company.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb

BOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_docs (
    kind text NOT NULL,
    company_id text NOT NULL,
    key text NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, company_id, key)
);
CREATE INDEX IF NOT EXISTS bot_docs_company_idx ON bot_docs (company_id, kind);
CREATE TABLE IF NOT EXISTS bot_seq (
    company_id text NOT NULL,
    name text NOT NULL,
    value bigint NOT NULL,
    PRIMARY KEY (company_id, name)
);
"""


class DocStore(Protocol):
    def put(self, kind: str, company_id: str, key: str, data: dict[str, Any]) -> None: ...

    def get(self, kind: str, company_id: str, key: str) -> dict[str, Any] | None: ...

    def all(self, kind: str, company_id: str) -> list[dict[str, Any]]: ...

    def delete(self, kind: str, company_id: str, key: str) -> None: ...

    def find(self, kind: str, field: str, value: str) -> list[tuple[str, dict[str, Any]]]:
        """Global search by a top-level string field -> [(company_id, document)]."""
        ...

    def next_seq(self, company_id: str, name: str, start: int = 1) -> int: ...


def _copy(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(d))
    return out


class MemoryDocStore:
    def __init__(self) -> None:
        self._docs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._seq: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    def put(self, kind: str, company_id: str, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._docs[(kind, company_id, key)] = _copy(data)

    def get(self, kind: str, company_id: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            d = self._docs.get((kind, company_id, key))
            return None if d is None else _copy(d)

    def all(self, kind: str, company_id: str) -> list[dict[str, Any]]:
        with self._lock:
            keys = sorted(k for k in self._docs if k[0] == kind and k[1] == company_id)
            return [_copy(self._docs[k]) for k in keys]

    def delete(self, kind: str, company_id: str, key: str) -> None:
        with self._lock:
            self._docs.pop((kind, company_id, key), None)

    def find(self, kind: str, field: str, value: str) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return [
                (c, _copy(d))
                for (k, c, _), d in self._docs.items()
                if k == kind and str(d.get(field)) == value
            ]

    def next_seq(self, company_id: str, name: str, start: int = 1) -> int:
        with self._lock:
            n = self._seq.get((company_id, name), start - 1) + 1
            self._seq[(company_id, name)] = n
            return n


class PgDocStore:
    """The same operations in PostgreSQL (tables bot_docs, bot_seq); autocommit connection."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn
        self._lock = threading.RLock()

    def init_schema(self) -> None:
        with self._lock:
            self.conn.execute(BOT_SCHEMA)

    def put(self, kind: str, company_id: str, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO bot_docs (kind, company_id, key, data) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (kind, company_id, key) DO UPDATE "
                "SET data = EXCLUDED.data, updated_at = now()",
                (kind, company_id, key, Jsonb(data)),
            )

    def get(self, kind: str, company_id: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT data FROM bot_docs WHERE kind = %s AND company_id = %s AND key = %s",
                (kind, company_id, key),
            ).fetchone()
        return None if row is None else dict(row[0])

    def all(self, kind: str, company_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM bot_docs WHERE kind = %s AND company_id = %s ORDER BY key",
                (kind, company_id),
            ).fetchall()
        return [dict(r[0]) for r in rows]

    def delete(self, kind: str, company_id: str, key: str) -> None:
        with self._lock:
            self.conn.execute(
                "DELETE FROM bot_docs WHERE kind = %s AND company_id = %s AND key = %s",
                (kind, company_id, key),
            )

    def find(self, kind: str, field: str, value: str) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT company_id, data FROM bot_docs WHERE kind = %s AND data ->> %s = %s",
                (kind, field, value),
            ).fetchall()
        return [(str(r[0]), dict(r[1])) for r in rows]

    def next_seq(self, company_id: str, name: str, start: int = 1) -> int:
        with self._lock:
            row = self.conn.execute(
                "INSERT INTO bot_seq (company_id, name, value) VALUES (%s, %s, %s) "
                "ON CONFLICT (company_id, name) DO UPDATE SET value = bot_seq.value + 1 "
                "RETURNING value",
                (company_id, name, start),
            ).fetchone()
        assert row is not None
        return int(row[0])
