"""Трасса в PostgreSQL. Нужен libpq: локально на этой Windows блокируется, гоняется в CI."""

from __future__ import annotations

from typing import Any

import pytest

from hrgid.flow.trace import PgTraceSink, TraceRecord


@pytest.mark.db
def test_trace_row_written_and_tenant_scoped(conn: Any) -> None:
    sink = PgTraceSink(conn)
    sink.write(
        TraceRecord(
            "A",
            "law",
            "answered",
            "вопрос",
            (1, 2),
            ("claude-haiku-5-5",),
            100,
            20,
            0.0123,
            850,
            False,
            3,
        )
    )
    sink.write(TraceRecord("B", "rules", "no_answer", "другой"))
    rows = sink.for_tenant("A")
    assert len(rows) == 1 and rows[0][0] == "law" and float(rows[0][2]) == pytest.approx(0.0123)
    assert sink.for_tenant("C") == []
