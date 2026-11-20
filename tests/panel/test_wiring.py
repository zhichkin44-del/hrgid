"""Панель подключена к потоку ответа: трассы -> лог вопросов (аналитика, расход), бюджет -> защита."""

from __future__ import annotations

from typing import Any

import pytest

from hrgid.bot.store import PgDocStore
from hrgid.flow.graph import AskRequest
from hrgid.flow.mask import Masker
from hrgid.llm.base import LLMResponse
from hrgid.llm.fake import FakeLLM
from hrgid.panel.deps import build_deps
from hrgid.panel.wiring import make_live_answerer, make_panel_traces
from hrgid.search import SearchHit

from .conftest import ADMIN, CO, make_env


class Stub:
    def search(self, question: str, tenants: Any) -> list[SearchHit]:
        return [
            SearchHit(
                id=1, tenant_id="law", article="115", part="", title="Отпуск", ref="ТК РФ, ст. 115",
                text="Ежегодный основной оплачиваемый отпуск — 28 календарных дней.", edition="",
                score=0.9,
            )
        ]  # fmt: skip


def llms() -> tuple[FakeLLM, FakeLLM]:
    def cheap(req: Any) -> str:
        return '{"route": "law"}'

    def main(req: Any) -> LLMResponse | str:
        return "Отпуск — 28 календарных дней [1]."

    return FakeLLM(cheap), FakeLLM(main)


def test_live_pipeline_traces_reach_panel_analytics_and_costs() -> None:
    env = make_env()
    cheap, main = llms()
    answerer = make_live_answerer(env.deps, cheap, main, Stub())
    pipe = answerer.inner  # type: ignore[attr-defined]
    pipe.masker = Masker(lambda t: [])
    a = answerer.ask(AskRequest("Сколько дней отпуска у меня в году?", CO))
    assert a.status in ("answered", "unverified", "no_answer")
    qs = env.deps.ops.questions(CO)
    assert len(qs) == 1 and qs[0]["topic"] == "Отпуск" and qs[0]["masked_question"]
    adm = env.login(ADMIN)
    assert "Сколько дней отпуска" in adm.get("/costs").text
    assert env.deps.analytics.has_data(CO)


def test_trace_sink_records_only_masked_text_and_scopes_tenant() -> None:
    env = make_env()
    from hrgid.flow.trace import TraceRecord

    sink = make_panel_traces(env.deps)
    sink.write(
        TraceRecord(CO, "law", "answered", "вопрос от [ФИО_1]", cost_rub=1.5, latency_ms=700)
    )
    sink.write(TraceRecord("northwind", "law", "answered", "чужой", cost_rub=9, latency_ms=1))
    assert [q["masked_question"] for q in env.deps.ops.questions(CO)] == ["вопрос от [ФИО_1]"]
    assert env.deps.ops.month_spend(CO, env.clock.now) == 1.5


def test_budget_gate_blocks_model_when_exhausted() -> None:
    env = make_env()
    env.deps.ops.log_question(CO, masked_question="x", cost_rub=10000, now=env.clock.now)
    cheap, main = llms()
    answerer = make_live_answerer(env.deps, cheap, main, Stub())
    a = answerer.ask(AskRequest("Сколько дней отпуска?", CO))
    assert a.status == "to_hr" and not cheap.requests and not main.requests


@pytest.mark.db
def test_panel_state_and_traces_in_postgres(pg_url: str) -> None:
    """Хранилище панели и таблица traces — в PostgreSQL (libpq; здесь блокируется, гоняется в CI)."""
    from hrgid.db import connect

    conn = connect(pg_url)
    store = PgDocStore(conn)
    store.init_schema()
    conn.execute("TRUNCATE bot_docs, bot_seq")
    deps = build_deps(store, pepper="p", bot_pepper="b")
    cheap, main = llms()
    answerer = make_live_answerer(deps, cheap, main, Stub(), conn=conn)
    answerer.inner.masker = Masker(lambda t: [])  # type: ignore[attr-defined]
    answerer.ask(AskRequest("Сколько дней отпуска?", CO))
    assert len(deps.ops.questions(CO)) == 1
    rows = conn.execute(
        "SELECT tenant_id, masked_question FROM traces WHERE tenant_id = %s", (CO,)
    ).fetchall()
    assert len(rows) == 1 and rows[0][0] == CO
    conn.close()
