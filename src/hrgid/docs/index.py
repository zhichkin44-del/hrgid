"""Items of the company's current rules -> the same search as for the law (the "company rules"
route)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg

from hrgid.docs.service import DocService
from hrgid.embed import Embedder
from hrgid.law.parse import Chunk
from hrgid.search import store_chunks

SCOPE = "rules"


@dataclass(frozen=True)
class RuleChunk(Chunk):
    """Rules item: title is the document name, article is the item number, part is the document
    id."""

    @property
    def ref(self) -> str:
        return f"{self.title}, п. {self.article}"

    @property
    def embed_text(self) -> str:
        return f"Правила компании. {self.title}, пункт {self.article}\n{self.text}"


def rule_chunks(svc: DocService, company: str, today: date) -> list[RuleChunk]:
    out: list[RuleChunk] = []
    for d in svc.documents(company):
        v = svc.active_version(company, d["doc_id"], today)
        if v is None or v.order_date is None or v.effective_from is None:
            continue
        edition = f"приказ № {v.order_no} от {v.order_date:%d.%m.%Y}, с {v.effective_from:%d.%m.%Y}"
        for it in v.items:
            if it.heading:
                continue
            out.append(
                RuleChunk(
                    company, it.number, d["doc_id"], 0, str(d["title"]), "", "", it.text, edition
                )
            )
    return out


def index_rules(
    conn: psycopg.Connection[Any], embedder: Embedder, svc: DocService, company: str, today: date
) -> int:
    """Re-indexing of one company's rules: old items are removed, current ones are written."""
    conn.execute("DELETE FROM chunks WHERE tenant_id = %s AND scope = %s", (company, SCOPE))
    chunks = rule_chunks(svc, company, today)
    store_chunks(conn, chunks, embedder, scope=SCOPE)
    return len(chunks)
