"""Stage 4 bot + stage 5 documents: /rules shows the company's current document version."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from docs.helpers import write_docx
from hrgid.bot.auth import AuthService
from hrgid.bot.desk import HrDesk
from hrgid.bot.kb import KbService
from hrgid.bot.repo import Repo
from hrgid.bot.store import MemoryDocStore
from hrgid.docs.provider import DocRulesProvider
from hrgid.docs.service import DocService

from .conftest import COMPANY
from .harness import Chat, Harness
from .test_scenarios import login


@pytest.fixture()
def h(repo: Repo, auth: AuthService, desk: HrDesk, kb: KbService) -> Harness:
    return Harness(repo, auth, desk, kb)


def _svc_with_rules(tmp: Path, company: str) -> DocService:
    svc = DocService(MemoryDocStore(), clock=lambda: datetime(2026, 11, 20, 12, 0))
    md = tmp / "p.md"
    md.write_text("1. Отпуск\n1.1. Отпуск — 31 календарный день.\n", encoding="utf-8")
    v = svc.upload(company, "p.docx", write_docx(md, tmp / "p.docx"), "admin", "Отпуск")
    svc.set_order(company, v.doc_id, 1, "12", date(2026, 1, 1), date(2026, 1, 10), "admin")
    return svc


def test_bot_rules_from_documents(h: Harness, tmp_path: Path) -> None:
    svc = _svc_with_rules(tmp_path, COMPANY)
    h.engine.rules = DocRulesProvider(svc, lambda: date(2026, 11, 18))
    c: Chat = login(h)
    c.say("/rules")
    assert c.buttons[0] == "Отпуск"
    c.tap("Отпуск")
    assert "Версия 1, приказ № 12 от 01.01.2026, действует с 10.01.2026" in c.text
    assert "1.1 Отпуск — 31 календарный день." in c.text


def test_bot_does_not_show_other_company_rules(h: Harness, tmp_path: Path) -> None:
    svc = _svc_with_rules(tmp_path, "other-company")
    h.engine.rules = DocRulesProvider(svc, lambda: date(2026, 11, 18))
    c = login(h)
    c.say("/rules")
    assert "Отпуск" not in c.buttons
