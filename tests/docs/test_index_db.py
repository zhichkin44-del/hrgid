"""Indexing of the rules into the common search (requires PostgreSQL: blocked on this Windows, so CI)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from hrgid.bot.store import MemoryDocStore
from hrgid.docs.index import SCOPE, index_rules, rule_chunks
from hrgid.docs.service import DocService

from .helpers import COMPANY, DRAFT, write_docx

TODAY = date(2026, 12, 1)


def _svc(tmp: Path, company: str = COMPANY) -> DocService:
    svc = DocService(MemoryDocStore(), clock=lambda: datetime(2026, 11, 20, 12, 0))
    v = svc.upload(company, "r.docx", write_docx(DRAFT, tmp / "r.docx"), "a", "Правила студии")
    svc.set_order(company, v.doc_id, 1, "1", date(2026, 11, 1), date(2026, 11, 10), "a")
    return svc


def test_rule_chunks_only_active_items_with_refs(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    chunks = rule_chunks(svc, COMPANY, TODAY)
    assert chunks and all(c.tenant_id == COMPANY for c in chunks)
    c = next(c for c in chunks if c.article == "6.1")
    assert c.ref == "Правила студии, п. 6.1" and "31 календарный день" in c.text
    assert "приказ № 1 от 01.11.2026" in c.edition
    assert not any(c.article == "1" for c in chunks)  # section headings are not indexed
    assert rule_chunks(svc, "other", TODAY) == []


@pytest.mark.db
def test_index_rules_searchable_and_isolated(conn: Any, tmp_path: Path) -> None:
    from hrgid.embed import HashEmbedder
    from hrgid.search import fts_search

    svc = _svc(tmp_path)
    n = index_rules(conn, HashEmbedder(), svc, COMPANY, TODAY)
    assert n == index_rules(conn, HashEmbedder(), svc, COMPANY, TODAY)  # idempotent
    hits = fts_search(conn, "сколько дней отпуск", [COMPANY], 5)
    assert any(h.article == "6.1" for h in hits)
    assert fts_search(conn, "сколько дней отпуск", ["other"], 5) == []
    rows = conn.execute("SELECT DISTINCT scope FROM chunks WHERE tenant_id = %s", (COMPANY,))
    assert [r[0] for r in rows.fetchall()] == [SCOPE]
