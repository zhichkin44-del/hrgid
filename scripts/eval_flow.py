"""Прогон набора через поток ответа (этап 2).

Воспроизведение записей (CI):  python scripts/eval_flow.py --provider claude
Живая запись (ключ владельца): python scripts/eval_flow.py --provider claude --record
  ANTHROPIC_API_KEY (claude), GIGACHAT_CREDENTIALS (gigachat), YANDEX_API_KEY + YANDEX_FOLDER_ID.
Отчёт: reports/stage2-<провайдер>-<дата>.md; порог «ответа нет» — из ADR-0006 (--threshold).
"""

from __future__ import annotations

import argparse
import time
from datetime import date

import hrgid  # noqa: F401  # libpq на Windows
from hrgid.db import connect, database_url, init_schema
from hrgid.embed import E5Embedder
from hrgid.flow.build import PROVIDERS, ROOT, make_llm, make_pipeline, make_searcher
from hrgid.flow.evalrun import load_rows, render_report, run_eval, summarize
from hrgid.law.ingest import load_chunks
from hrgid.search import store_chunks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=PROVIDERS, default="claude")
    ap.add_argument("--record", action="store_true", help="живой вызов и запись ответов")
    ap.add_argument("--reranker", default="")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--set", default="retrieval.jsonl")
    args = ap.parse_args()

    eval_file = ROOT / "data" / "evals" / args.set
    rows = load_rows(eval_file)
    conn = connect(database_url(ROOT / ".pgdata_eval"))
    init_schema(conn)
    have = conn.execute("SELECT count(*) FROM chunks").fetchone()
    chunks, stats = load_chunks(ROOT / "data" / "raw")
    if have is None or have[0] != stats.chunks:
        conn.execute("TRUNCATE chunks RESTART IDENTITY")
        store_chunks(conn, chunks, E5Embedder())
    from hrgid.rerank import CANDIDATES

    key = args.reranker or next(iter(CANDIDATES))
    llm = make_llm(args.provider, eval_file, args.record)
    pipe = make_pipeline(llm, make_searcher(conn, key), args.threshold)
    t = time.perf_counter()
    results = run_eval(pipe, rows)
    stamp = date.today().isoformat()
    source = f"{args.provider} ({'живая запись' if args.record else 'воспроизведение записей'})"
    report = render_report(summarize(results), source, stamp)
    report += f"\nВремя прогона: {time.perf_counter() - t:.0f} с. Провалы:\n"
    report += "\n".join(f"- {r.id}: {r.status} {r.note}" for r in results if not r.ok) + "\n"
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"stage2-{args.provider}-{stamp}.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
