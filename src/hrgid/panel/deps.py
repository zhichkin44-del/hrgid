"""Сборка зависимостей панели: хранилище, сервисы этапов 2–5, сервисы панели."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from hrgid.bot.auth import AuthService
from hrgid.bot.desk import HrDesk
from hrgid.bot.kb import KbService
from hrgid.bot.repo import Repo
from hrgid.bot.store import DocStore
from hrgid.docs.service import DocService
from hrgid.docs.tkcheck import TkChecker
from hrgid.panel.journal import Journal
from hrgid.panel.mailer import LogMailer, Mailer
from hrgid.panel.ops import OpsStore
from hrgid.panel.security import PanelAuth
from hrgid.panel.services import (
    Analytics,
    CompanyAdmin,
    KbAdmin,
    Ratings,
    StaffService,
    Undo,
)


@dataclass
class PanelDeps:
    store: DocStore
    repo: Repo
    auth: PanelAuth
    bot_auth: AuthService
    desk: HrDesk
    kb: KbService
    docs: DocService
    checker: TkChecker | None
    journal: Journal
    ops: OpsStore
    staff: StaffService
    kbadmin: KbAdmin
    ratings: Ratings
    company: CompanyAdmin
    analytics: Analytics
    undo: Undo
    clock: Callable[[], datetime]
    bot_username: str = "hrgid_bot"
    secure_cookie: bool = False
    # качество по версиям набора (П8, админ): [{version, date, hit, ...}]
    quality: Callable[[], list[dict[str, str]]] = field(default=lambda: [])
    extras: dict[str, Any] = field(default_factory=dict)


def build_deps(
    store: DocStore,
    *,
    checker_factory: Callable[[DocService], TkChecker] | None = None,
    mailer: Mailer | None = None,
    clock: Callable[[], datetime] | None = None,
    pepper: str = "",
    bot_pepper: str | None = None,
    bot_username: str = "hrgid_bot",
    secure_cookie: bool = False,
) -> PanelDeps:
    clk = clock or (lambda: datetime.now(UTC))
    repo = Repo(store)
    kb = KbService(repo)
    kbadmin = KbAdmin(repo, kb)

    def affected(company: str, doc_id: str) -> int:
        title = next((d["title"] for d in store.all("doc", company) if d["doc_id"] == doc_id), "")
        return kbadmin.mark_by_doc(company, title, "Изменилась версия документа компании")

    docs = DocService(store, clock=clk, affected_answers=affected)
    desk = HrDesk(repo, kb)
    bot_auth = AuthService(repo, pepper=bot_pepper)
    ops = OpsStore(store, repo, clk)
    auth = PanelAuth(store, mailer or LogMailer(), pepper=pepper)
    journal = Journal(store)
    undo = Undo(store)
    staff = StaffService(repo, bot_auth, desk, store)
    deps = PanelDeps(
        store=store,
        repo=repo,
        auth=auth,
        bot_auth=bot_auth,
        desk=desk,
        kb=kb,
        docs=docs,
        checker=checker_factory(docs) if checker_factory else None,
        journal=journal,
        ops=ops,
        staff=staff,
        kbadmin=kbadmin,
        ratings=Ratings(repo, store),
        company=CompanyAdmin(store, repo, journal, auth),
        analytics=Analytics(repo, ops, desk, kb),
        undo=undo,
        clock=clk,
        bot_username=bot_username,
        secure_cookie=secure_cookie,
    )

    def restore_kb(company: str, p: dict[str, Any]) -> None:
        kbadmin.set_archived(company, int(p["id"]), False)

    def restore_emp(company: str, p: dict[str, Any]) -> None:
        staff.enable(company, str(p["id"]))

    undo.restore["kb.archive"] = restore_kb
    undo.restore["emp.disable"] = restore_emp
    undo.finish["doc.delete_version"] = lambda c, p: docs.delete_version(
        c, str(p["doc_id"]), int(p["number"]), str(p["user"]), clk().date()
    )
    undo.finish["emp.purge"] = lambda c, p: desk.purge_employee(c, str(p["id"]))
    return deps
