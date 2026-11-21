from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from hrgid.bot.auth import AuthService
from hrgid.bot.desk import HrDesk
from hrgid.bot.kb import KbService
from hrgid.bot.models import CompanySettings, Employee
from hrgid.bot.repo import Repo
from hrgid.bot.store import DocStore, MemoryDocStore, PgDocStore

MSK = timezone(timedelta(hours=3))
COMPANY = "zhichkin"
HR_CHAT = 9001
OLGA_TG = 5001


def msk(y: int, m: int, d: int, h: int = 12, mi: int = 0) -> datetime:
    """The moment in Moscow time, in UTC."""
    return datetime(y, m, d, h, mi, tzinfo=MSK).astimezone(UTC)


NOW = msk(2026, 11, 18, 15, 40)  # Wednesday, HR working hours


@pytest.fixture(params=["memory", pytest.param("pg", marks=pytest.mark.db)])
def store(request: pytest.FixtureRequest) -> Iterator[DocStore]:
    if request.param == "memory":
        yield MemoryDocStore()
        return
    from hrgid.db import connect

    pg_url: str = request.getfixturevalue("pg_url")
    conn: Any = connect(pg_url)
    pg = PgDocStore(conn)
    pg.init_schema()
    conn.execute("TRUNCATE bot_docs, bot_seq")
    yield pg
    conn.close()


def default_settings() -> CompanySettings:
    return CompanySettings(
        company_id=COMPANY,
        company_name="Студия Жичкина",
        hr_name="Анна Воронова",
        hr_chat_id=HR_CHAT,
        urgent_contact="Анна, HR: +7 900 000-00-00",
        code_prefix="ЖС",
    )


@pytest.fixture()
def repo(store: DocStore) -> Repo:
    r = Repo(store)
    r.save_settings(default_settings())
    return r


def make_employee(
    repo: Repo, emp_id: str = "e1", name: str = "Ольга Смирнова", company: str = COMPANY, **kw: Any
) -> Employee:
    base: dict[str, Any] = {
        "id": emp_id,
        "company_id": company,
        "full_name": name,
        "position": "дизайнер",
        "hire_date": date(2024, 3, 11),
        "schedule": "5/2",
        "salary": Decimal(80000),
        "profile_date": date(2026, 11, 12),
    }
    base.update(kw)
    e = Employee(**base)
    repo.save_employee(e)
    return e


@pytest.fixture()
def olga(repo: Repo) -> Employee:
    return make_employee(repo)


@pytest.fixture()
def auth(repo: Repo) -> AuthService:
    return AuthService(repo, pepper="test-pepper")


@pytest.fixture()
def kb(repo: Repo) -> KbService:
    return KbService(repo)


@pytest.fixture()
def desk(repo: Repo, kb: KbService) -> HrDesk:
    return HrDesk(repo, kb)
