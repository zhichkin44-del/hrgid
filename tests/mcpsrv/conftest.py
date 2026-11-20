"""Стенд тестов MCP: две компании, поиск в памяти с фильтром по tenant_id, Fake-модель проверки."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from hrgid.bot.store import MemoryDocStore
from hrgid.docs.tkcheck import Norm
from hrgid.flow.mask import Masker
from hrgid.law.parse import LAW_TENANT
from hrgid.llm.base import LLMRequest
from hrgid.llm.fake import FakeLLM
from hrgid.mcp.clause import LlmClauseChecker
from hrgid.mcp.gateway import McpGateway
from hrgid.mcp.tokens import McpTokens
from hrgid.search import SearchHit, visible_tenants

A, B = "alpha", "beta"

CHUNKS: list[SearchHit] = [
    SearchHit(
        1, LAW_TENANT, "115", "", "Ежегодные оплачиваемые отпуска", "ТК РФ, ст. 115",
        "Ежегодный основной оплачиваемый отпуск предоставляется работникам "
        "продолжительностью 28 календарных дней.", "ред. 2026", 0.0,
    ),
    SearchHit(
        2, LAW_TENANT, "70", "", "Испытание при приёме на работу", "ТК РФ, ст. 70",
        "Испытание не может превышать трёх месяцев.", "ред. 2026", 0.0,
    ),
    SearchHit(
        3, A, "4.1", "d1", "Правила alpha", "Правила alpha, п. 4.1",
        "Отпуск сотрудника Иванова Ивана Ивановича, телефон +7 900 123-45-67.", "приказ 1", 0.0,
    ),
    SearchHit(
        4, B, "9.9", "d2", "Правила beta", "Правила beta, п. 9.9",
        "Секрет компании beta: премия по особому коэффициенту.", "приказ 7", 0.0,
    ),
]  # fmt: skip


def _words(t: str) -> set[str]:
    return {w[:5] for w in re.findall(r"\w+", t.lower()) if len(w) > 3}


class MemSearcher:
    """Как настоящий поиск: видны только переданные компании (+ закон), пустой список — ничего."""

    def search(self, question: str, tenants: Sequence[str]) -> list[SearchHit]:
        allowed = set(visible_tenants(tenants))
        q = _words(question)
        return [h for h in CHUNKS if h.tenant_id in allowed and q & _words(h.title + " " + h.text)]


def fake_names(text: str) -> list[tuple[int, int]]:
    name = "Иванова Ивана Ивановича"
    i = text.find(name)
    if i < 0:
        i = text.find("Иванова")
        name = "Иванова"
    return [(i, i + len(name))] if i >= 0 else []


class OneNorm:
    def find(self, text: str, k: int = 4) -> list[Norm]:
        return [Norm("ТК РФ, ст. 70", "70", "Испытание не может превышать трёх месяцев.")]


def judge(req: LLMRequest) -> str:
    body = req.messages[0].content
    if "шесть месяцев" in body:
        return (
            '{"verdict": "risk", "explanation": "Дольше трёх месяцев.", '
            '"article": "ст. 70", "suggestion": "до трёх месяцев"}'
        )
    return '{"verdict": "ok", "explanation": "Не противоречит.", "article": ""}'


@dataclass
class Rig:
    store: MemoryDocStore
    tokens: McpTokens
    gw: McpGateway
    llm: FakeLLM
    tok: dict[str, str] = field(default_factory=dict)


@pytest.fixture()
def rig() -> Rig:
    store = MemoryDocStore()
    tokens = McpTokens(store, lambda: datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    llm = FakeLLM(judge)
    gw = McpGateway(
        tokens, MemSearcher(), Masker(name_finder=fake_names), LlmClauseChecker(llm, OneNorm())
    )
    r = Rig(store, tokens, gw, llm)
    r.tok[A], _ = tokens.issue(A, "Claude Code", "admin@alpha")
    r.tok[B], _ = tokens.issue(B, "Claude Desktop", "admin@beta")
    return r


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"
