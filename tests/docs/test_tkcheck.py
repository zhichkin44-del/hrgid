from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from hrgid.bot.store import MemoryDocStore
from hrgid.docs.parse import DocError
from hrgid.docs.report import export_report, to_html
from hrgid.docs.service import DocService
from hrgid.docs.tkcheck import TkChecker
from hrgid.llm.base import LLMRequest
from hrgid.llm.fake import FakeLLM

from .helpers import COMPANY, VIOLATIONS, KeywordNormSource, make_fake, write_docx


def _setup(tmp: Path) -> tuple[DocService, TkChecker, str]:
    svc = DocService(MemoryDocStore(), clock=lambda: datetime(2026, 11, 20, 12, 0))
    data = write_docx(VIOLATIONS, tmp / "v.docx")
    v = svc.upload(COMPANY, "v.docx", data, "admin", "Правила студии")
    return svc, TkChecker(make_fake(), KeywordNormSource(), svc), v.doc_id


def test_every_item_evaluated_and_three_risks_found(tmp_path: Path) -> None:
    svc, chk, doc = _setup(tmp_path)
    rep = chk.check_version(COMPANY, doc, 1, "admin")
    v = svc.version(COMPANY, doc, 1)
    assert len(rep.items) == len(v.checkable) > 25
    assert all(i.verdict in ("ok", "better", "risk") for i in rep.items)
    assert all(i.explanation for i in rep.items)
    assert {i.number for i in rep.risks} == {"4.1", "5.1", "6.1"}
    for r in rep.risks:
        assert r.suggestion and r.article
        assert any(n.startswith("ТК РФ, ст. " + r.article.split()[-1]) for n in r.norms)


def test_risk_with_foreign_article_is_not_accepted(tmp_path: Path) -> None:
    svc, _, doc = _setup(tmp_path)
    bad = FakeLLM(
        lambda r: json.dumps(
            {"verdict": "risk", "explanation": "x", "article": "ст. 999", "suggestion": "y"}
        )
    )
    rep = TkChecker(bad, KeywordNormSource(), svc).check_version(COMPANY, doc, 1, "a")
    assert rep.count("risk") == 0 and rep.count("unclear") == len(rep.items)
    assert len(bad.requests) == 2 * len(rep.items)  # one repeat attempt per item


def test_garbage_answer_is_unclear(tmp_path: Path) -> None:
    svc, _, doc = _setup(tmp_path)
    chk = TkChecker(FakeLLM(lambda r: "не знаю"), KeywordNormSource(), svc)
    assert chk.check_version(COMPANY, doc, 1, "a").count("unclear") > 0


def test_prompt_contains_norms_and_marks_item_as_data(tmp_path: Path) -> None:
    svc, _, doc = _setup(tmp_path)
    llm = make_fake()
    TkChecker(llm, KeywordNormSource(), svc).check_version(COMPANY, doc, 1, "a")
    req: LLMRequest = next(r for r in llm.requests if "шесть месяцев" in r.messages[0].content)
    assert "ст. 70" in req.messages[0].content and "<<<" in req.messages[0].content
    assert req.purpose == "tkcheck"


def test_fix_item_creates_version_and_rechecks(tmp_path: Path) -> None:
    svc, chk, doc = _setup(tmp_path)
    chk.check_version(COMPANY, doc, 1, "admin")
    nv, rep = chk.fix_item(
        COMPANY, doc, 1, "5.1",
        "Работнику устанавливается испытание продолжительностью до трёх месяцев.", "admin",
    )  # fmt: skip
    assert nv.number == 2 and nv.parent == 1
    assert {i.number for i in rep.risks} == {"4.1", "6.1"}
    assert svc.version(COMPANY, doc, 1).item("5.1").text.startswith("Рядовому")  # type: ignore[union-attr]
    with pytest.raises(DocError):
        chk.fix_item(COMPANY, doc, 2, "5.1", "  ", "admin")
    with pytest.raises(DocError):
        chk.fix_item(COMPANY, doc, 2, "99.9", "текст", "admin")


def test_accept_risk_requires_comment_and_flag_and_persists(tmp_path: Path) -> None:
    svc, chk, doc = _setup(tmp_path)
    chk.check_version(COMPANY, doc, 1, "admin")
    with pytest.raises(DocError) as e:
        chk.accept_risk(COMPANY, doc, 1, "6.1", "  ", "boss", True)
    assert e.value.code == "comment"
    with pytest.raises(DocError) as e2:
        chk.accept_risk(COMPANY, doc, 1, "6.1", "причина", "boss", False)
    assert e2.value.code == "understood"
    with pytest.raises(DocError) as e3:
        chk.accept_risk(COMPANY, doc, 1, "1.1", "причина", "boss", True)
    assert e3.value.code == "not_risk"
    rep = chk.accept_risk(COMPANY, doc, 1, "6.1", "договорённость с коллективом", "boss", True)
    assert {i.number for i in rep.open_risks} == {"4.1", "5.1"}
    saved = chk.report(COMPANY, doc, 1)
    assert saved is not None
    acc = next(i for i in saved.items if i.number == "6.1").accepted
    assert acc is not None and acc["author"] == "boss" and acc["at"].startswith("2026-11-20")
    assert any(a["action"] == "tk.accept_risk" for a in svc.audit_log(COMPANY))


def test_accepted_risk_carries_to_unchanged_item_in_new_version(tmp_path: Path) -> None:
    _, chk, doc = _setup(tmp_path)
    chk.check_version(COMPANY, doc, 1, "admin")
    chk.accept_risk(COMPANY, doc, 1, "6.1", "причина", "boss", True)
    _, rep = chk.fix_item(COMPANY, doc, 1, "5.1", "Испытание продолжительностью до три месяц", "a")
    r61 = next(i for i in rep.items if i.number == "6.1")
    assert r61.accepted is not None and r61.accepted["author"] == "boss"
    assert next(i for i in rep.items if i.number == "4.1").accepted is None


def test_tenant_isolation_of_reports(tmp_path: Path) -> None:
    _, chk, doc = _setup(tmp_path)
    chk.check_version(COMPANY, doc, 1, "admin")
    assert chk.report("other", doc, 1) is None


def test_report_html_and_export(tmp_path: Path) -> None:
    _, chk, doc = _setup(tmp_path)
    rep = chk.check_version(COMPANY, doc, 1, "admin")
    html = to_html(rep, "Правила студии", "Приказ № 1.")
    assert "риск нарушения" in html and "Пункт 5.1" in html
    path, fmt = export_report(rep, "Правила студии", tmp_path / "out", "Приказ № 1.")
    assert fmt in ("pdf", "html") and path.exists() and path.stat().st_size > 500
    if fmt == "pdf":
        assert path.read_bytes().startswith(b"%PDF")
