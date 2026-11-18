from datetime import date
from decimal import Decimal as D

import pytest

from hrgid.calc.common import CalcError
from hrgid.calc.overtime import overtime_pay, weekend_work_pay

SAL = D(79500)
NORM = D(159)


def test_r4_per_day_split() -> None:
    days = [(date(2026, 11, 10), D(3)), (date(2026, 11, 11), D(1)), (date(2026, 11, 12), D("2.5"))]
    r = overtime_pay(SAL, NORM, days)
    assert r.details["rate"] == D("500.00")
    assert r.details["hours_x1_5"] == D(5)
    assert r.details["hours_x2"] == D("1.5")
    assert r.value == D("5250.00")


def test_r4_exactly_two_hours_is_all_x15() -> None:
    r = overtime_pay(SAL, NORM, [(date(2026, 11, 10), D(2))])
    assert r.value == D("1500.00")
    assert r.details["hours_x2"] == D(0)


def test_r4_annual_limit_120() -> None:
    r = overtime_pay(SAL, NORM, [(date(2026, 11, 10), D(3))], hours_before=D(119))
    assert r.details["hours_x1_5"] == D(1)
    assert r.details["hours_x2"] == D(2)
    assert r.value == D("2750.00")
    r2 = overtime_pay(SAL, NORM, [(date(2026, 11, 10), D(2))], hours_before=D(130))
    assert r2.value == D("2000.00")


def test_r4_time_off() -> None:
    r = overtime_pay(SAL, NORM, [(date(2026, 11, 10), D(3))], time_off=True)
    assert r.details["rest_hours"] == D(3)
    assert r.value == D("1500.00")  # одинарная оплата, отдых не оплачивается


@pytest.mark.parametrize("hours", [D(0), D(-1), D(25)])
def test_r4_bad_hours(hours: D) -> None:
    with pytest.raises(CalcError):
        overtime_pay(SAL, NORM, [(date(2026, 11, 10), hours)])


def test_r4_bad_inputs() -> None:
    with pytest.raises(CalcError):
        overtime_pay(SAL, NORM, [])
    with pytest.raises(CalcError):
        overtime_pay(D(0), NORM, [(date(2026, 11, 10), D(1))])
    with pytest.raises(CalcError):
        overtime_pay(SAL, D(0), [(date(2026, 11, 10), D(1))])


def test_r5_double() -> None:
    r = weekend_work_pay(date(2026, 11, 14), D(8), SAL, NORM)
    assert r.value == D("8000.00")
    assert r.details["rest_days"] == 0


def test_r5_single_plus_rest() -> None:
    r = weekend_work_pay(date(2026, 11, 14), D(8), SAL, NORM, variant="rest_day")
    assert r.value == D("4000.00")
    assert r.details["rest_days"] == 1


def test_r5_salaried_within_norm_single_over_salary() -> None:
    r = weekend_work_pay(date(2026, 11, 14), D(8), SAL, NORM, salaried_within_norm=True)
    assert r.value == D("4000.00")


def test_r5_holiday_and_transferred_day_off() -> None:
    assert weekend_work_pay(date(2026, 11, 4), D(4), SAL, NORM).value == D("4000.00")
    assert weekend_work_pay(date(2026, 1, 9), D(1), SAL, NORM).value == D("1000.00")


def test_r5_workday_rejected() -> None:
    with pytest.raises(CalcError, match="не выходной"):
        weekend_work_pay(date(2026, 11, 13), D(8), SAL, NORM)


@pytest.mark.parametrize("hours", [D(0), D(25)])
def test_r5_hours_range(hours: D) -> None:
    with pytest.raises(CalcError):
        weekend_work_pay(date(2026, 11, 14), hours, SAL, NORM)


def test_r5_24_hours_ok() -> None:
    assert weekend_work_pay(date(2026, 11, 14), D(24), SAL, NORM).value == D("24000.00")


def test_r5_bad_inputs() -> None:
    with pytest.raises(CalcError):
        weekend_work_pay(date(2026, 11, 14), D(8), D(0), NORM)
    with pytest.raises(CalcError):
        weekend_work_pay(date(2026, 11, 14), D(8), SAL, NORM, variant="x")  # type: ignore[arg-type]
