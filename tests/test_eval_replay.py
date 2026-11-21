"""Run of the main set on recorded answers (CI). Needs the DB, e5 and the reranker: slow.

No recordings for the current prompts: MissingRecording, the test is red (intentionally, ADR-0003).
Until the first live recording exists, tests/recordings/PENDING lies there and the test is skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PENDING = ROOT / "tests" / "recordings" / "PENDING"


@pytest.mark.slow
@pytest.mark.db
@pytest.mark.skipif(PENDING.exists(), reason="ждёт первой живой записи (make record)")
def test_replay_main_set_meets_stage2_thresholds() -> None:
    import hrgid  # noqa: F401
    from hrgid.db import connect, database_url, init_schema
    from hrgid.embed import E5Embedder
    from hrgid.flow.build import make_llm, make_pipeline, make_searcher
    from hrgid.flow.evalrun import load_rows, run_eval, summarize, verdict
    from hrgid.law.ingest import load_chunks
    from hrgid.rerank import CANDIDATES
    from hrgid.search import store_chunks

    eval_file = ROOT / "data" / "evals" / "retrieval.jsonl"
    conn = connect(database_url(ROOT / ".pgdata_eval"))
    init_schema(conn)
    chunks, _ = load_chunks(ROOT / "data" / "raw")
    conn.execute("TRUNCATE chunks RESTART IDENTITY")
    store_chunks(conn, chunks, E5Embedder())
    llm = make_llm("openrouter", eval_file, record=False)  # recordings of a live run on Haiku
    pipe = make_pipeline(llm, make_searcher(conn, next(iter(CANDIDATES))), None)
    s = summarize(run_eval(pipe, load_rows(eval_file)))
    assert all(verdict(s).values()), s
