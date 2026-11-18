"""Замер поиска: hit@5 (вектор / гибрид / гибрид + переранжирование) и задержка на CPU.

python scripts/eval_retrieval.py select   — выбор переранжировщика на настроечной части
python scripts/eval_retrieval.py report <ключ> — замер на основной части (JSON в reports/)
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import hrgid  # noqa: F401  # libpq на Windows
from hrgid.db import connect, database_url, init_schema
from hrgid.embed import E5Embedder
from hrgid.law.ingest import load_chunks
from hrgid.rerank import CANDIDATES, CrossEncoderReranker
from hrgid.search import SearchHit, hybrid_search, store_chunks, vector_search

ROOT = Path(__file__).resolve().parent.parent
K = 5
POOL = 20
TENANTS = ["studio"]  # компания без своих документов: видит только закон


def load_set(name: str) -> list[dict[str, Any]]:
    path = ROOT / "data" / "evals" / name
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def hit(hits: list[SearchHit], gold: list[str]) -> bool:
    return bool({h.article for h in hits[:K]} & set(gold))


def rerank_keys(mode: str, args: list[str]) -> list[str]:
    """Ключи переранжировщиков: select — все; report <ключ> — один; --no-rerank — ни одного."""
    if mode == "select":
        return list(CANDIDATES)
    if args[0] == "--no-rerank":
        return []
    return [args[0]]


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def main() -> int:
    mode = sys.argv[1]
    conn = connect(database_url(ROOT / ".pgdata_eval"))
    init_schema(conn)
    emb = E5Embedder()
    have = conn.execute("SELECT count(*) FROM chunks").fetchone()
    chunks, stats = load_chunks(ROOT / "data" / "raw")
    if have is None or have[0] != stats.chunks:
        t = time.perf_counter()
        conn.execute("TRUNCATE chunks RESTART IDENTITY")
        store_chunks(conn, chunks, emb)
        print(f"индекс: {stats.chunks} фрагментов за {time.perf_counter() - t:.0f} с")
    if mode == "select":
        questions = [q for q in load_set("tuning.jsonl") if q["kind"] == "law"]
    else:
        questions = load_set("retrieval.jsonl")
    keys = rerank_keys(mode, sys.argv[2:])
    law = [q for q in questions if q["kind"] == "law"]
    out: dict[str, Any] = {"n_law": len(law)}

    def vec(q: str) -> list[SearchHit]:
        return vector_search(conn, emb.embed_query(q), TENANTS, K)

    def hyb(q: str) -> list[SearchHit]:
        return hybrid_search(conn, q, TENANTS, emb, k=K, pool=POOL)

    runs: dict[str, Any] = {"vector": vec, "hybrid": hyb}
    for key in keys:
        rr = CrossEncoderReranker(CANDIDATES[key])
        runs[f"hybrid+rerank[{key}]"] = lambda q, rr=rr: hybrid_search(
            conn, q, TENANTS, emb, k=K, pool=POOL, reranker=rr
        )
    for name, fn in runs.items():
        fn(law[0]["question"])  # прогрев
        hits = sum(hit(fn(q["question"]), q["gold_articles"]) for q in law)
        times: list[float] = []
        for q in questions:
            t = time.perf_counter()
            fn(q["question"])
            times.append((time.perf_counter() - t) * 1000)
        out[name] = {
            "hit5": hits / len(law),
            "hits": hits,
            "p50_ms": statistics.median(times),
            "p95_ms": pct(times, 0.95),
            "n_queries": len(times),
        }
        print(name, out[name], flush=True)
    stamp = date.today().isoformat()
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / f"retrieval-{mode}-{stamp}.json").write_text(
        json.dumps(
            {"date": stamp, "cpu": platform.processor(), "py": platform.python_version(), **out},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
