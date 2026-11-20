"""Пункты действующих правил компании -> тот же поиск, что и закон (маршрут «правила компании»)."""

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
    """Пункт правил: title — название документа, article — номер пункта, part — id документа."""

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
    """Переиндексация правил одной компании: старые пункты убираются, действующие записываются."""
    conn.execute("DELETE FROM chunks WHERE tenant_id = %s AND scope = %s", (company, SCOPE))
    chunks = rule_chunks(svc, company, today)
    store_chunks(conn, chunks, embedder, scope=SCOPE)
    return len(chunks)
