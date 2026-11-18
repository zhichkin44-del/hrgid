from datetime import date
from decimal import Decimal

import pytest

from hrgid.calc.calendar import is_day_off, month_norm_hours, working_days_in_month
from hrgid.calc.common import CalcError


def test_november_2026_norm() -> None:
    assert working_days_in_month(2026, 11) == 20
    assert month_norm_hours(2026, 11) == Decimal(159)


# (рабочих дней, часов) по месяцам — таблица производственного календаря consultant.ru
TABLE_2026 = [
    (15, 120),
    (19, 152),
    (21, 168),
    (22, 175),
    (19, 151),
    (21, 167),
    (23, 184),
    (21, 168),
    (22, 176),
    (22, 176),
    (20, 159),
    (22, 176),
]
TABLE_2025 = [
    (17, 136),
    (20, 160),
    (21, 167),
    (22, 175),
    (18, 144),
    (19, 151),
    (23, 184),
    (21, 168),
    (22, 176),
    (23, 184),
    (19, 151),
    (22, 176),
]


@pytest.mark.parametrize(("year", "table"), [(2026, TABLE_2026), (2025, TABLE_2025)])
def test_year_tables(year: int, table: list[tuple[int, int]]) -> None:
    for month, (days, hours) in enumerate(table, start=1):
        assert working_days_in_month(year, month) == days
        assert month_norm_hours(year, month) == Decimal(hours)
    assert sum(d for d, _ in table) == 247
    assert sum(h for _, h in table) == 1972


def test_day_off() -> None:
    assert is_day_off(date(2026, 11, 14))  # суббота
    assert is_day_off(date(2026, 11, 4))  # праздник
    assert is_day_off(date(2026, 1, 9))  # перенос
    assert not is_day_off(date(2026, 11, 3))  # сокращённый, но рабочий
    assert not is_day_off(date(2025, 11, 1))  # рабочая суббота


def test_unknown_year() -> None:
    with pytest.raises(CalcError):
        is_day_off(date(2031, 1, 1))
