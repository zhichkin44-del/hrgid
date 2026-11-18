from __future__ import annotations

import importlib.util
from pathlib import Path

from hrgid.rerank import CANDIDATES

_spec = importlib.util.spec_from_file_location(
    "eval_retrieval", Path(__file__).resolve().parent.parent / "scripts" / "eval_retrieval.py"
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_select_uses_all_candidates() -> None:
    assert _mod.rerank_keys("select", []) == list(CANDIDATES)


def test_report_with_key_uses_only_that_key() -> None:
    key = next(iter(CANDIDATES))
    assert _mod.rerank_keys("report", [key]) == [key]


def test_report_no_rerank_flag_gives_no_rerankers() -> None:
    assert _mod.rerank_keys("report", ["--no-rerank"]) == []
