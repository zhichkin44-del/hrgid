"""Адаптер поиска: hybrid_search (вектор, полнотекст, RRF, переранжирование) для потока."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from hrgid.embed import Embedder
from hrgid.search import Reranker, SearchHit, hybrid_search


class HybridSearcher:
    def __init__(
        self, conn: Any, embedder: Embedder, reranker: Reranker | None = None, k: int = 6
    ) -> None:
        self.conn = conn
        self.embedder = embedder
        self.reranker = reranker
        self.k = k

    def search(self, question: str, tenants: Sequence[str]) -> list[SearchHit]:
        return hybrid_search(
            self.conn, question, tenants, self.embedder, k=self.k, reranker=self.reranker
        )
