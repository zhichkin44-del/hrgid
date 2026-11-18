from datetime import date

import pytest

from hrgid.calc.common import CalcError
from hrgid.calc.terms import last_working_day, payment_deadline, probation_end


def test_r7_basic() -> None:
    r = last_working_day(date(2026, 11, 18))
    assert r.value == date(2026, 12, 2)
    assert r.article == "ст. 80 ТК РФ"


def test_r7_weekend_hint() -> None:
    r = last_working_day(date(2026, 11, 21))  # 5 декабря — суббота
    assert r.value == date(2026, 12, 5)
    assert r.details["next_working_day"] == date(2026, 12, 7)


def test_r7_leap_and_year_end() -> None:
    assert last_working_day(date(2028, 2, 20)).value == date(2028, 3, 5)
    assert last_working_day(date(2026, 12, 25)).value == date(2027, 1, 8)


def test_r7_custom_notice_and_error() -> None:
    assert last_working_day(date(2026, 11, 18), notice_days=3).value == date(2026, 11, 21)
    with pytest.raises(CalcError):
        last_working_day(date(2026, 11, 18), notice_days=0)


def test_r8_vacation() -> None:
    r = payment_deadline("vacation", date(2026, 12, 14))
    assert r.value == date(2026, 12, 11)
    assert r.article.startswith("ст. 136")


def test_r8_final() -> None:
    r = payment_deadline("final", date(2026, 12, 2))
    assert r.value == date(2026, 12, 2)
    assert r.article.startswith("ст. 140")


def test_r8_salary_shift_before_weekend() -> None:
    # период до 31.10.2026, 15 дней -> 15.11 (воскресенье) -> накануне, пятница 13.11
    r = payment_deadline("salary", date(2026, 10, 31))
    assert r.value == date(2026, 11, 13)


def test_r8_salary_on_workday() -> None:
    assert payment_deadline("salary", date(2026, 11, 30)).value == date(2026, 12, 15)


def test_r8_bad_kind() -> None:
    with pytest.raises(CalcError):
        payment_deadline("bonus", date(2026, 12, 14))


def test_r9_three_months() -> None:
    r = probation_end(date(2026, 11, 18), 3)
    assert r.value == date(2027, 2, 17)


def test_r9_absence_shifts() -> None:
    assert probation_end(date(2026, 11, 18), 3, absence_days=5).value == date(2027, 2, 22)


def test_r9_month_end_clamp() -> None:
    assert probation_end(date(2026, 11, 30), 3).value == date(2027, 2, 27)
    assert probation_end(date(2027, 11, 30), 3).value == date(2028, 2, 28)


def test_r9_leap_start() -> None:
    assert probation_end(date(2024, 2, 29), 1).value == date(2024, 3, 28)


def test_r9_more_than_three_months_error() -> None:
    with pytest.raises(CalcError, match="3 месяц"):
        probation_end(date(2026, 11, 18), 4)


def test_r9_senior_up_to_six() -> None:
    assert probation_end(date(2026, 11, 18), 6, senior=True).value == date(2027, 5, 17)
    with pytest.raises(CalcError):
        probation_end(date(2026, 11, 18), 7, senior=True)


def test_r9_short_contract() -> None:
    with pytest.raises(CalcError):
        probation_end(date(2026, 11, 18), 1, contract_months=1)  # до двух месяцев — нельзя
    with pytest.raises(CalcError):
        probation_end(date(2026, 11, 18), 1, contract_months=5)  # 2-6 мес. — только 2 недели
    r = probation_end(date(2026, 11, 18), 0, contract_months=5, weeks=2)
    assert r.value == date(2026, 12, 1)


def test_r9_bad_months() -> None:
    with pytest.raises(CalcError):
        probation_end(date(2026, 11, 18), 0)
