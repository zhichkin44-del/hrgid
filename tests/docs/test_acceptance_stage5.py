"""Acceptance of stage 5 (SPEC-09 §16, line 5), mechanics on a Fake model with recordings.

Live figures (the real model + law index) come after the API key and stage 1 in CI.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from hrgid.bot.store import MemoryDocStore
from hrgid.docs.service import DocService
from hrgid.docs.tkcheck import TkChecker

from .helpers import COMPANY, DRAFT, VIOLATIONS, KeywordNormSource, make_fake, write_docx

EXPECTED = {"4.1": "ст. 136", "5.1": "ст. 70", "6.1": "ст. 115"}


def _run(md: Path, tmp: Path) -> tuple[DocService, TkChecker, str]:
    svc = DocService(MemoryDocStore(), clock=lambda: datetime(2026, 11, 20, 12, 0))
    v = svc.upload(COMPANY, "r.docx", write_docx(md, tmp / "r.docx"), "admin", "Правила студии")
    return svc, TkChecker(make_fake(), KeywordNormSource(), svc), v.doc_id


def test_all_three_planted_violations_found_with_right_articles(tmp_path: Path) -> None:
    svc, chk, doc = _run(VIOLATIONS, tmp_path)
    rep = chk.check_version(COMPANY, doc, 1, "admin")
    found = {i.number: i.article for i in rep.risks}
    assert found == EXPECTED  # 3 of 3, no false positives
    assert len(rep.items) == len(svc.version(COMPANY, doc, 1).checkable)


def test_draft_rules_have_no_risks_and_vacation_is_better_than_law(tmp_path: Path) -> None:
    _, chk, doc = _run(DRAFT, tmp_path)
    rep = chk.check_version(COMPANY, doc, 1, "admin")
    assert rep.count("risk") == 0 and rep.count("unclear") == 0
    assert [i.number for i in rep.items if i.verdict == "better"] == ["6.1"]


def test_dump_second_checker_input(tmp_path: Path) -> None:
    """Prepares material for a second reviewer: the risk + the text of the article it refers to."""
    _, chk, doc = _run(VIOLATIONS, tmp_path)
    rep = chk.check_version(COMPANY, doc, 1, "admin")
    norms = {n.ref: n.text for n in KeywordNormSource().norms}
    pack = [
        {
            "item": i.number, "rule_text": i.text, "verdict": i.verdict,
            "explanation": i.explanation, "article": i.article, "suggestion": i.suggestion,
            "article_texts": [norms[r] for r in i.norms if r.startswith("ТК РФ, ст. " + i.article.split()[-1] + ",")],
        }
        for i in rep.risks
    ]  # fmt: skip
    assert len(pack) == 3 and all(p["article_texts"] for p in pack)
    out = tmp_path / "second.json"
    out.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))[0]["item"] == "4.1"
