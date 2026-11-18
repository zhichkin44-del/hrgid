"""Хранение фрагментов и поиск: вектор, полнотекст (русский), слияние RRF, переранжирование.

Каждый запрос фильтруется по tenant_id: пустой список компаний — пустой результат;
текст закона (tenant «law») виден всем компаниям.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import psycopg

from hrgid.embed import Embedder
from hrgid.law.parse import LAW_TENANT, Chunk

_COLS = "id, tenant_id, article, part, title, ref, text, edition"


@dataclass(frozen=True)
class SearchHit:
    id: int
    tenant_id: str
    article: str
    part: str
    title: str
    ref: str
    text: str
    edition: str
    score: float

    @property
    def rerank_text(self) -> str:
        return f"Статья {self.article}. {self.title}\n{self.text}"


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


def visible_tenants(tenants: Sequence[str]) -> list[str]:
    """Список компаний + закон. Пустой список — нет контекста компании, значит ничего не видно."""
    if not tenants:
        return []
    return sorted({*tenants, LAW_TENANT})


def store_chunks(
    conn: psycopg.Connection[Any],
    chunks: Sequence[Chunk],
    embedder: Embedder,
    scope: str = "law",
    batch: int = 64,
) -> None:
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        vecs = embedder.embed_passages([c.embed_text for c in part])
        rows = [
            (
                c.tenant_id,
                scope,
                c.article,
                c.part or "",
                c.sub,
                c.ref,
                c.title,
                c.chapter,
                c.section,
                c.text,
                c.edition,
                c.content_hash,
                np.asarray(v, dtype=np.float32),
            )
            for c, v in zip(part, vecs, strict=True)
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (tenant_id, scope, article, part, sub, ref, title, chapter,
                                    section, text, edition, content_hash, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id, scope, article, part, sub) DO UPDATE SET
                    ref = EXCLUDED.ref, title = EXCLUDED.title, chapter = EXCLUDED.chapter,
                    section = EXCLUDED.section, text = EXCLUDED.text,
                    edition = EXCLUDED.edition, content_hash = EXCLUDED.content_hash,
                    embedding = EXCLUDED.embedding
                """,
                rows,
            )


def _hit(row: tuple[Any, ...], score: float) -> SearchHit:
    return SearchHit(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], score)


def vector_search(
    conn: psycopg.Connection[Any], qvec: np.ndarray, tenants: Sequence[str], k: int
) -> list[SearchHit]:
    allowed = visible_tenants(tenants)
    if not allowed:
        return []
    rows = conn.execute(
        f"SELECT {_COLS}, 1 - (embedding <=> %s) AS sim FROM chunks "  # noqa: S608
        "WHERE tenant_id = ANY(%s) ORDER BY embedding <=> %s LIMIT %s",
        (np.asarray(qvec, dtype=np.float32), allowed, np.asarray(qvec, dtype=np.float32), k),
    ).fetchall()
    return [_hit(r[:8], float(r[8])) for r in rows]


_WORD = re.compile(r"\w+", re.UNICODE)


def or_query(question: str) -> str:
    """Слова вопроса через «или» (русский словарь сам отбросит стоп-слова и приведёт к основам)."""
    words = [w for w in _WORD.findall(question.lower()) if len(w) > 1]
    return " | ".join(dict.fromkeys(words))


def fts_search(
    conn: psycopg.Connection[Any], question: str, tenants: Sequence[str], k: int
) -> list[SearchHit]:
    allowed = visible_tenants(tenants)
    q = or_query(question)
    if not allowed or not q:
        return []
    rows = conn.execute(
        f"SELECT {_COLS}, ts_rank_cd(tsv, query, 32) AS rnk "  # noqa: S608
        "FROM chunks, to_tsquery('russian', %s) AS query "
        "WHERE tenant_id = ANY(%s) AND tsv @@ query ORDER BY rnk DESC, id LIMIT %s",
        (q, allowed, k),
    ).fetchall()
    return [_hit(r[:8], float(r[8])) for r in rows]


def rrf_merge(rankings: Sequence[Sequence[SearchHit]], k: int = 60) -> list[SearchHit]:
    scores: dict[int, float] = {}
    first: dict[int, SearchHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, 1):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)
            first.setdefault(hit.id, hit)
    order = sorted(scores, key=lambda i: (-scores[i], i))
    return [_replace_score(first[i], scores[i]) for i in order]


def _replace_score(hit: SearchHit, score: float) -> SearchHit:
    return SearchHit(
        hit.id, hit.tenant_id, hit.article, hit.part, hit.title, hit.ref, hit.text,
        hit.edition, score,
    )  # fmt: skip


def rerank(query: str, hits: Sequence[SearchHit], reranker: Reranker, k: int) -> list[SearchHit]:
    if not hits:
        return []
    scores = reranker.score(query, [h.rerank_text for h in hits])
    ranked = sorted(zip(hits, scores, strict=True), key=lambda p: -p[1])
    return [_replace_score(h, float(s)) for h, s in ranked[:k]]


def hybrid_search(
    conn: psycopg.Connection[Any],
    question: str,
    tenants: Sequence[str],
    embedder: Embedder,
    k: int = 6,
    pool: int = 30,
    reranker: Reranker | None = None,
) -> list[SearchHit]:
    v = vector_search(conn, embedder.embed_query(question), tenants, pool)
    f = fts_search(conn, question, tenants, pool)
    merged = rrf_merge([v, f])
    if reranker is None:
        return merged[:k]
    return rerank(question, merged[:pool], reranker, k)
