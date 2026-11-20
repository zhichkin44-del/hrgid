"""Стенд тестов панели: два тенанта, часы, письма в памяти, Fake-модель проверки на ТК."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hrgid.bot.models import CompanySettings, Employee
from hrgid.bot.store import MemoryDocStore
from hrgid.docs.service import DocService
from hrgid.docs.tkcheck import TkChecker
from hrgid.panel.app import create_panel_app
from hrgid.panel.deps import PanelDeps, build_deps
from hrgid.panel.mailer import MemoryMailer

MSK = timezone(timedelta(hours=3))
CO = "zhichkin"
OTHER = "northwind"
ADMIN = "maria@zhichkin.test"
HR = "anna@zhichkin.test"
HR2 = "oleg@zhichkin.test"
OTHER_HR = "boss@northwind.test"


def msk(y: int, m: int, d: int, h: int = 12, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=MSK).astimezone(UTC)


NOW = msk(2026, 11, 18, 15, 40)


def _helpers() -> ModuleType:
    """Помощники тестов этапа 5 (источник норм по ключевым словам, Fake-модель)."""
    path = Path(__file__).resolve().parents[1] / "docs" / "helpers.py"
    spec = importlib.util.spec_from_file_location("stage5_helpers", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stage5_helpers"] = mod
    spec.loader.exec_module(mod)
    return mod


H = _helpers()


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@dataclass
class Env:
    store: MemoryDocStore
    deps: PanelDeps
    app: Any
    mailer: MemoryMailer
    clock: Clock
    clients: list[TestClient] = field(default_factory=list)

    def anon(self) -> TestClient:
        c = TestClient(self.app, follow_redirects=True, raise_server_exceptions=False)
        self.clients.append(c)
        return c

    def login(self, email: str, client: TestClient | None = None) -> TestClient:
        c = client or self.anon()
        r = c.post("/login", data={"email": email})
        assert r.status_code == 200, r.text[:200]
        code = self.mailer.last_code(email)
        r = c.post("/login/code", data={"code": code})
        assert r.status_code == 200
        assert "hrgid_sid" in c.cookies
        return c

    def employee(self, company: str = CO, name: str = "Ольга Смирнова", **kw: Any) -> Employee:
        e = Employee(
            id=kw.pop("emp_id", "e" + str(len(self.deps.repo.employees(company)) + 1)),
            company_id=company,
            full_name=name,
            position=kw.pop("position", "дизайнер"),
            **kw,
        )
        self.deps.repo.save_employee(e)
        return e

    def connect(self, e: Employee, tg: int) -> Employee:
        e = e.model_copy(update={"telegram_id": tg, "consent_at": self.clock.now})
        self.deps.repo.save_employee(e)
        return e

    def ticket(self, e: Employee, text: str, *, confidential: bool = False) -> Any:
        return self.deps.desk.create(e, text, self.clock.now, confidential=confidential)


def make_env(checker: bool = True) -> Env:
    store = MemoryDocStore()
    clock = Clock()
    mailer = MemoryMailer()

    def mk_checker(docs: DocService) -> TkChecker:
        return TkChecker(H.make_fake(), H.KeywordNormSource(), docs)

    deps = build_deps(
        store,
        checker_factory=mk_checker if checker else None,
        mailer=mailer,
        clock=clock,
        pepper="test-pepper",
        bot_pepper="bot-pepper",
    )
    for co, name in ((CO, "Студия Жичкина"), (OTHER, "Нортвинд")):
        deps.repo.save_settings(
            CompanySettings(
                company_id=co,
                company_name=name,
                hr_name="Анна Воронова",
                hr_chat_id=9001 if co == CO else 9002,
                urgent_contact="Анна, HR: +7 900 000-00-00",
                code_prefix="ЖС" if co == CO else "НВ",
            )
        )
    deps.auth.add_user(CO, ADMIN, "Мария Орлова", "admin")
    deps.auth.add_user(CO, HR, "Анна Воронова", "hr")
    deps.auth.add_user(CO, HR2, "Олег Петров", "hr", away=True)
    deps.auth.add_user(OTHER, OTHER_HR, "Борис Север", "admin")
    app = create_panel_app(deps)
    return Env(store, deps, app, mailer, clock)


@pytest.fixture()
def env() -> Env:
    return make_env()


@pytest.fixture()
def hr(env: Env) -> TestClient:
    return env.login(HR)


@pytest.fixture()
def admin(env: Env) -> TestClient:
    return env.login(ADMIN)


Make = Callable[[], Env]
