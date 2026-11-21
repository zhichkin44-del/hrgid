from __future__ import annotations

from datetime import timedelta

from hrgid.bot import hours
from hrgid.bot.auth import MAX_FAILS, AuthService, looks_like_code, normalize_code
from hrgid.bot.models import Employee
from hrgid.bot.repo import Repo

from .conftest import COMPANY, NOW, make_employee, msk


def test_sla_in_working_hours(repo: Repo) -> None:
    s = repo.settings(COMPANY)
    # 15:40 + 4 working hours: 3 h 20 min today until 19:00 and 40 min tomorrow -> 10:40 (screen Б2.6)
    due = hours.add_working_hours(NOW, 4, s)
    assert due == msk(2026, 11, 19, 10, 40)
    assert hours.deadline_phrase(NOW, due, s) == "завтра до 10:40"
    # outside working hours: count from the next opening
    night = msk(2026, 11, 17, 21, 36)
    assert not hours.is_open(night, s)
    assert hours.add_working_hours(night, 4, s) == msk(2026, 11, 18, 14, 0)
    assert hours.open_phrase(night, s) == "завтра с 10:00"
    # Friday evening -> Monday
    assert hours.add_working_hours(msk(2026, 11, 20, 18, 0), 4, s) == msk(2026, 11, 23, 13, 0)
    assert hours.hours_range(s) == "пн–пт с 10:00 до 19:00 (мск)"


def test_sla_same_day(repo: Repo) -> None:
    s = repo.settings(COMPANY)
    t = msk(2026, 11, 18, 11, 0)
    assert hours.deadline_phrase(t, hours.add_working_hours(t, 4, s), s) == "сегодня до 15:00"


def test_weekend_is_not_working(repo: Repo) -> None:
    s = repo.settings(COMPANY)
    assert not hours.is_workday(msk(2026, 11, 21).date(), s)  # Saturday
    assert not hours.is_open(msk(2026, 11, 21, 12), s)


def test_code_normalization() -> None:
    assert looks_like_code("жс-4817-т9к2")
    assert not looks_like_code("привет, как дела")
    assert normalize_code("ЖС-4817-Т9К2") == normalize_code(" жс-4817-т9к2 ")
    # Latin look-alikes of Cyrillic letters
    assert normalize_code("ЖС-4817-AB12") == normalize_code("ЖС-4817-АВ12")


def _employee_with_code(repo: Repo, auth: AuthService) -> tuple[Employee, str]:
    e = make_employee(repo)
    return e, auth.issue_code(COMPANY, e.id, NOW)


def test_code_stored_as_hash_and_login(repo: Repo, auth: AuthService) -> None:
    e, code = _employee_with_code(repo, auth)
    stored = repo.codes_of(COMPANY, e.id)[0]
    assert code not in stored.code_hash and stored.code_hash == auth.hash_code(code)
    assert code.startswith("ЖС-")
    r = auth.check_code(1, code.lower(), NOW)
    assert r.status == "ok" and r.employee is not None and r.code is not None
    emp, old = auth.bind(r.employee, r.code, 1, NOW, consent=True)
    assert emp.telegram_id == 1 and emp.consent_at == NOW and old is None
    # one-time code
    assert auth.check_code(2, code, NOW).status == "bad"


def test_five_fails_block_for_15_minutes(repo: Repo, auth: AuthService) -> None:
    e, code = _employee_with_code(repo, auth)
    for i in range(1, MAX_FAILS):
        r = auth.check_code(7, f"ЖС-0000-ААА{i}", NOW)
        assert r.status == "bad" and r.fails == i
    r = auth.check_code(7, "ЖС-0000-ААА9", NOW)
    assert r.status == "blocked" and r.blocked_until == NOW + timedelta(minutes=15)
    # even a correct code is not accepted while blocked
    assert auth.check_code(7, code, NOW + timedelta(minutes=5)).status == "blocked"
    # after 15 minutes it is allowed again; the counter is reset
    assert auth.check_code(7, code, NOW + timedelta(minutes=16)).status == "ok"
    # the block applies to a specific Telegram account; another account is not affected
    assert auth.blocked_until(8, NOW) is None


def test_reissue_revokes_old_code(repo: Repo, auth: AuthService) -> None:
    e, old = _employee_with_code(repo, auth)
    new = auth.issue_code(COMPANY, e.id, NOW)
    assert auth.check_code(1, old, NOW).status == "bad"
    assert auth.check_code(1, new, NOW).status == "ok"


def test_disabled_employee_gets_closed(repo: Repo, auth: AuthService) -> None:
    e, code = _employee_with_code(repo, auth)
    repo.save_employee(e.model_copy(update={"status": "disabled"}))
    assert auth.check_code(1, code, NOW).status == "closed"
