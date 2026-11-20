"""Подключение панели к боевому потоку ответа: трассы -> лог вопросов (П7, П8) и PostgreSQL,
бюджет -> защита от вызова модели (сценарий 5)."""

from __future__ import annotations

from typing import Any

from hrgid.bot.engine import Answerer
from hrgid.flow.graph import FlowConfig, Pipeline, Searcher
from hrgid.flow.trace import PgTraceSink, TraceSink
from hrgid.llm.base import LLMClient
from hrgid.panel.deps import PanelDeps
from hrgid.panel.ops import BudgetGatedAnswerer, PanelTraceSink


def make_panel_traces(deps: PanelDeps, conn: Any = None) -> TraceSink:
    """Приёмник трасс: в лог вопросов панели и (если есть соединение) в таблицу traces."""
    inner = PgTraceSink(conn) if conn is not None else None
    return PanelTraceSink(deps.ops, inner)


def make_live_answerer(
    deps: PanelDeps,
    cheap_llm: LLMClient,
    main_llm: LLMClient,
    searcher: Searcher,
    *,
    conn: Any = None,
    config: FlowConfig | None = None,
) -> Answerer:
    """Поток ответа для бота: трассы идут в панель, при исчерпанном бюджете модель не зовётся."""
    pipe = Pipeline(
        cheap_llm, main_llm, searcher, traces=make_panel_traces(deps, conn), config=config
    )
    return BudgetGatedAnswerer(pipe, deps.ops, deps.clock)
