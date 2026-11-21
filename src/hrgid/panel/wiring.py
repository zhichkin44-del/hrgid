"""Connects the panel to the production answer flow: traces -> question log (П7, П8) and PostgreSQL,
budget -> protection against a model call (scenario 5).
"""

from __future__ import annotations

from typing import Any

from hrgid.bot.engine import Answerer
from hrgid.flow.graph import FlowConfig, Pipeline, Searcher
from hrgid.flow.trace import PgTraceSink, TraceSink
from hrgid.llm.base import LLMClient
from hrgid.panel.deps import PanelDeps
from hrgid.panel.ops import BudgetGatedAnswerer, PanelTraceSink


def make_panel_traces(deps: PanelDeps, conn: Any = None) -> TraceSink:
    """Trace sink: into the panel's question log and, if there is a connection, into the traces table."""
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
    """Answer flow for the bot: traces go to the panel; when the budget is exhausted, the model is not called."""
    pipe = Pipeline(
        cheap_llm, main_llm, searcher, traces=make_panel_traces(deps, conn), config=config
    )
    return BudgetGatedAnswerer(pipe, deps.ops, deps.clock)
