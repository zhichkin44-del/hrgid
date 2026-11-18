from datetime import date
from decimal import Decimal as D

import pytest

from hrgid.calc.common import CalcError, add_months
from hrgid.calc.vacation import accrued_vacation, leave_compensation, vacation_pay


def test_add_months_clamps() -> None:
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2025, 11, 30), 3) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 15), -2) == date(2025, 11, 15)


def test_r1_full_year() -> None:
    r = vacation_pay(D(1200000), date(2026, 12, 14), 14)
    assert r.details["avg_daily"] == D("3412.97")
    assert r.value == D("47781.58")
    assert r.details["ndfl"] == D(6212)
    assert r.details["net"] == D("41569.58")
    assert r.details["pay_deadline"] == date(2026, 12, 11)
    assert "29,3" in r.formula and r.article.startswith("ст. 139")
    assert "1200000" in r.substituted


def test_r1_excluded_days_in_month() -> None:
    r = vacation_pay(D(540000), date(2026, 9, 7), 28, excluded_days={(2026, 3): 10})
    assert r.details["avg_daily"] == D("1578.26")
    assert r.value == D("44191.28")


def test_r1_leap_february_hire() -> None:
    r = vacation_pay(D(600000), date(2024, 9, 2), 14, hire_date=date(2024, 2, 10))
    assert r.details["avg_daily"] == D("3061.12")
    assert r.value == D("42855.68")


def test_r1_first_month_partial() -> None:
    r = vacation_pay(D(90000), date(2026, 11, 23), 5, hire_date=date(2026, 10, 20))
    assert r.details["avg_daily"] == D("7935.15")
    assert r.value == D("39675.75")


@pytest.mark.parametrize("days", [0, 57, -1])
def test_r1_days_range(days: int) -> None:
    with pytest.raises(CalcError):
        vacation_pay(D(1000), date(2026, 12, 1), days)


def test_r1_bad_input() -> None:
    with pytest.raises(CalcError):
        vacation_pay(D(0), date(2026, 12, 1), 14)
    with pytest.raises(CalcError):  # принят в месяц отпуска — расчётного периода нет
        vacation_pay(D(1000), date(2026, 12, 1), 14, hire_date=date(2026, 12, 1))
    with pytest.raises(CalcError):
        vacation_pay(D(1000), date(2026, 12, 1), 14, excluded_days={(2026, 3): 99})


def test_r1_ndfl_rounding_half_up() -> None:
    # 439500 / (29,3 x 12) = 1250,00; 1250 x 0,13 = 162,5 -> 163 (не банковское)
    r = vacation_pay(D(439500), date(2026, 12, 1), 1)
    assert r.value == D("1250.00")
    assert r.details["ndfl"] == D(163)


def test_r2_compensation() -> None:
    r = leave_compensation(D(700000), date(2026, 11, 20), D(11), hire_date=date(2025, 3, 17))
    assert r.details["avg_daily"] == D("1990.90")
    assert r.value == D("21899.90")
    assert r.details["ndfl"] == D(2847)
    assert r.article.startswith("ст. 127")


def test_r2_negative_days() -> None:
    with pytest.raises(CalcError):
        leave_compensation(D(1000), date(2026, 11, 20), D(-1))


def test_r3_ten_months_leftover_below_15() -> None:
    r = accrued_vacation(date(2026, 1, 12), date(2026, 11, 18))
    assert r.details["months"] == 10
    assert r.value == D("23.33")


def test_r3_leftover_15_counts_as_month() -> None:
    # 2026-01-01 .. 2026-01-15 включительно = 15 дней
    r = accrued_vacation(date(2026, 1, 1), date(2026, 1, 15))
    assert r.details["months"] == 1
    r2 = accrued_vacation(date(2026, 1, 1), date(2026, 1, 14))
    assert r2.details["months"] == 0
    assert r2.value == D(0)


def test_r3_leftover_thirty() -> None:
    r = accrued_vacation(date(2025, 3, 20), date(2026, 11, 18))
    assert r.details["months"] == 20
    assert r.value == D("46.67")


def test_r3_leap_hire_feb_29() -> None:
    r = accrued_vacation(date(2024, 2, 29), date(2025, 2, 28))
    assert r.details["months"] == 12
    assert r.value == D("28.00")


def test_r3_used_days_and_balance() -> None:
    r = accrued_vacation(date(2025, 1, 10), date(2026, 1, 9), used_days=D(14))
    assert r.value == D("28.00")
    assert r.details["balance"] == D("14.00")


def test_r3_excluded_days_shift() -> None:
    r = accrued_vacation(date(2025, 1, 10), date(2026, 1, 9), excluded_days=30)
    assert r.details["months"] == 11


def test_r3_bad_dates() -> None:
    with pytest.raises(CalcError):
        accrued_vacation(date(2026, 5, 1), date(2026, 4, 30))


def test_r3_right_to_leave_after_six_months() -> None:
    assert accrued_vacation(date(2026, 1, 12), date(2026, 5, 1)).details["can_take_now"] is False
    assert accrued_vacation(date(2026, 1, 12), date(2026, 8, 1)).details["can_take_now"] is True


def test_r1_fully_excluded_month_skipped() -> None:
    # март 2026 — весь месяц исключён: 11 полных месяцев
    r = vacation_pay(D(322300), date(2026, 9, 7), 10, excluded_days={(2026, 3): 31})
    assert r.details["divisor"] == D("322.3000")
    assert r.details["avg_daily"] == D("1000.00")
