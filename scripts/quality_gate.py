"""Ворота качества CI: основной набор на записанных ответах (ADR-0003). Нужны БД, e5, закон.

Коды выхода: 0 — пройдено или пропущено (нет первой живой записи, предупреждение в журнал CI),
1 — ворота красные (пороги не достигнуты или нет записи для изменённого промпта).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hrgid.flow.gate import PENDING_WARNING, evaluate_gate, recordings_pending  # noqa: E402


def main() -> int:
    if recordings_pending(ROOT):
        print(f"::warning title=Ворота качества пропущены::{PENDING_WARNING}")
        return 0

    import hrgid  # noqa: F401
    from hrgid.db import connect, database_url, init_schema
    from hrgid.embed import E5Embedder
    from hrgid.flow.build import make_llm, make_pipeline, make_searcher
    from hrgid.flow.evalrun import load_rows
    from hrgid.law.ingest import load_chunks
    from hrgid.rerank import CANDIDATES
    from hrgid.search import store_chunks

    eval_file = ROOT / "data" / "evals" / "retrieval.jsonl"
    conn = connect(database_url(ROOT / ".pgdata_eval"))
    init_schema(conn)
    chunks, _ = load_chunks(ROOT / "data" / "raw")
    conn.execute("TRUNCATE chunks RESTART IDENTITY")
    store_chunks(conn, chunks, E5Embedder())
    llm = make_llm("claude", eval_file, record=False)
    pipe = make_pipeline(llm, make_searcher(conn, next(iter(CANDIDATES))), None)
    res = evaluate_gate(pipe, load_rows(eval_file))
    print(f"{res.status}: {res.message}")
    if not res.ok:
        print(f"::error title=Ворота качества красные::{res.message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
