"""Трасса запроса (§11): маршрут, фрагменты, модель, токены, стоимость, задержка (traces)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

TRACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    ts timestamptz NOT NULL DEFAULT now(),
    channel text NOT NULL DEFAULT 'web',
    route text NOT NULL,
    status text NOT NULL,
    masked_question text NOT NULL,
    fragment_ids bigint[] NOT NULL DEFAULT '{}',
    models text[] NOT NULL DEFAULT '{}',
    input_tokens int NOT NULL DEFAULT 0,
    output_tokens int NOT NULL DEFAULT 0,
    cost_rub numeric(12, 4) NOT NULL DEFAULT 0,
    latency_ms int NOT NULL DEFAULT 0,
    cache_hit boolean NOT NULL DEFAULT false,
    llm_calls int NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS traces_tenant_ts_idx ON traces (tenant_id, ts DESC);
"""


@dataclass(frozen=True)
class TraceRecord:
    tenant_id: str
    route: str
    status: str
    masked_question: str
    fragment_ids: tuple[int, ...] = ()
    models: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cost_rub: float = 0.0
    latency_ms: int = 0
    cache_hit: bool = False
    llm_calls: int = 0
    channel: str = "web"


class TraceSink(Protocol):
    def write(self, rec: TraceRecord) -> None: ...


@dataclass
class MemoryTraceSink:
    records: list[TraceRecord] = field(default_factory=list)

    def write(self, rec: TraceRecord) -> None:
        self.records.append(rec)

    def for_tenant(self, tenant_id: str) -> list[TraceRecord]:
        return [r for r in self.records if r.tenant_id == tenant_id]


class PgTraceSink:
    def __init__(self, conn: Any) -> None:
        self.conn = conn
        conn.execute(TRACE_SCHEMA)

    def write(self, rec: TraceRecord) -> None:
        self.conn.execute(
            "INSERT INTO traces (tenant_id, channel, route, status, masked_question, "
            "fragment_ids, models, input_tokens, output_tokens, cost_rub, latency_ms, "
            "cache_hit, llm_calls) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                rec.tenant_id, rec.channel, rec.route, rec.status, rec.masked_question,
                list(rec.fragment_ids), list(rec.models), rec.input_tokens, rec.output_tokens,
                round(rec.cost_rub, 4), rec.latency_ms, rec.cache_hit, rec.llm_calls,
            ),
        )  # fmt: skip

    def for_tenant(self, tenant_id: str) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = self.conn.execute(
            "SELECT route, status, cost_rub, latency_ms FROM traces "
            "WHERE tenant_id = %s ORDER BY id",
            (tenant_id,),
        ).fetchall()
        return rows
