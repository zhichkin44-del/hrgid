from __future__ import annotations

from typing import Any

import psycopg

from hrgid.embed import HashEmbedder
from hrgid.law.parse import Chunk
from hrgid.search import (
    SearchHit,
    fts_search,
    hybrid_search,
    rrf_merge,
    store_chunks,
    vector_search,
)

EMB = HashEmbedder()


def mk(article: str, text: str, tenant: str = "law", title: str = "Т") -> Chunk:
    return Chunk(tenant, article, "1", 0, title, "Глава", "Раздел", text, "2026")


def load(conn: psycopg.Connection[Any]) -> None:
    store_chunks(
        conn,
        [
            mk("114", "Работникам предоставляются ежегодные оплачиваемые отпуска", title="Отпуска"),
            mk("80", "Работник имеет право расторгнуть трудовой договор предупредив за две недели"),
            mk("152", "Сверхурочная работа оплачивается за первые два часа не менее чем в полуторном размере"),
            mk("900", "Правило компании: отпуск тридцать один день", tenant="acme"),
            mk("901", "Секретный регламент другой фирмы про отпуск", tenant="other"),
        ],
        EMB,
        scope="law",
    )


def test_store_is_idempotent(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    load(conn)
    row = conn.execute("SELECT count(*) FROM chunks").fetchone()
    assert row is not None and row[0] == 5


def test_vector_and_fts_find_relevant(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    q = "сколько оплачиваемых отпусков у работников"
    v = vector_search(conn, EMB.embed_query(q), ["acme"], 3)
    f = fts_search(conn, q, ["acme"], 3)
    assert v[0].article == "114"
    assert f[0].article == "114"


def test_tenant_isolation_all_search_paths(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    q = "секретный регламент другой фирмы про отпуск"
    for hits in (
        vector_search(conn, EMB.embed_query(q), ["acme"], 10),
        fts_search(conn, q, ["acme"], 10),
        hybrid_search(conn, q, ["acme"], EMB, k=10),
    ):
        assert hits
        assert "901" not in {h.article for h in hits}
        assert {h.tenant_id for h in hits} <= {"law", "acme"}


def test_company_sees_own_and_law(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    hits = hybrid_search(conn, "отпуск тридцать один день", ["acme"], EMB, k=5)
    assert {"900", "114"} & {h.article for h in hits}
    assert hits[0].article == "900"


def test_rrf_merge_prefers_items_in_both_lists() -> None:
    def h(i: int) -> SearchHit:
        return SearchHit(i, "law", str(i), "", "ТК", "", "", "", 0.0)

    merged = rrf_merge([[h(1), h(2), h(3)], [h(3), h(2), h(4)]], k=60)
    ids = [x.id for x in merged]
    assert ids[:2] == [2, 3] or ids[:2] == [3, 2]
    assert set(ids) == {1, 2, 3, 4}


def test_empty_tenant_list_returns_nothing(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    assert vector_search(conn, EMB.embed_query("отпуск"), [], 5) == []
    assert fts_search(conn, "отпуск", [], 5) == []


class ReverseReranker:
    """Подставной переранжировщик: оценка тем выше, чем длиннее текст."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(len(t)) for t in texts]


def test_rerank_reorders_and_keeps_tenant_filter(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    hits = hybrid_search(
        conn, "отпуск работникам", ["acme"], EMB, k=2, reranker=ReverseReranker(), pool=10
    )
    assert len(hits) == 2
    assert hits[0].score >= hits[1].score
    assert "901" not in {h.article for h in hits}


def test_fts_handles_stopword_only_and_punctuation(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    assert fts_search(conn, "и в на", ["acme"], 5) == []
    assert fts_search(conn, "отпуск?! (тридцать) & | ' ; --", ["acme"], 5)


def test_unknown_tenant_cannot_see_other_company(conn: psycopg.Connection[Any]) -> None:
    load(conn)
    hits = hybrid_search(conn, "секретный регламент", ["nobody"], EMB, k=10)
    assert {h.tenant_id for h in hits} <= {"law"}
