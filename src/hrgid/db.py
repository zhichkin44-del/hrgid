"""Подключение к PostgreSQL: DATABASE_URL или встроенный pgserver (локальная разработка)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pgserver
import psycopg
from pgvector.psycopg import register_vector

EMBED_DIM = 384

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    scope text NOT NULL DEFAULT 'law',
    article text NOT NULL,
    part text NOT NULL DEFAULT '',
    sub int NOT NULL DEFAULT 0,
    ref text NOT NULL,
    title text NOT NULL DEFAULT '',
    chapter text NOT NULL DEFAULT '',
    section text NOT NULL DEFAULT '',
    text text NOT NULL,
    edition text NOT NULL DEFAULT '',
    content_hash text NOT NULL,
    embedding vector({EMBED_DIM}) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('russian', coalesce(title, '') || ' ' || text)
    ) STORED,
    UNIQUE (tenant_id, scope, article, part, sub)
);
CREATE INDEX IF NOT EXISTS chunks_tenant_idx ON chunks (tenant_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_emb_idx ON chunks USING hnsw (embedding vector_cosine_ops);
"""

_servers: dict[str, Any] = {}


def database_url(data_dir: Path | None = None) -> str:
    """URL БД: DATABASE_URL, иначе встроенный сервер в каталоге data_dir (по умолчанию .pgdata)."""
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    path = str((data_dir or Path(".pgdata")).resolve())
    if path not in _servers:
        _servers[path] = pgserver.get_server(path, cleanup_mode="stop")  # type: ignore[attr-defined]
    url: str = _servers[path].get_uri()
    return url


def connect(url: str) -> psycopg.Connection[Any]:
    conn: psycopg.Connection[Any] = psycopg.connect(url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def init_schema(conn: psycopg.Connection[Any]) -> None:
    conn.execute(SCHEMA)
