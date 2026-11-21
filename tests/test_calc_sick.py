from datetime import date
from decimal import Decimal as D

import pytest

from hrgid.calc.common import CalcError
from hrgid.calc.sick import sick_pay

START, END = date(2026, 3, 2), date(2026, 3, 11)
E2 = {2024: D(1000000), 2025: D(1200000)}


def test_r6_basic_80_percent() -> None:
    r = sick_pay(E2, START, END, D(6))
    assert r.details["avg_daily"] == D("3013.70")
    assert r.details["percent"] == D("0.80")
    assert r.details["daily"] == D("2410.96")
    assert r.value == D("24109.60")
    assert r.details["employer_part"] == D("7232.88")
    assert r.details["fund_part"] == D("16876.72")
    assert r.details["days"] == 10
    assert "730" in r.formula


@pytest.mark.parametrize(
    ("years", "pct"),
    [
        (D("0.5"), "0.60"),
        (D("4.99"), "0.60"),
        (D(5), "0.80"),
        (D("7.99"), "0.80"),
        (D(8), "1.00"),
        (D(30), "1.00"),
    ],
)
def test_r6_percent_boundaries(years: D, pct: str) -> None:
    assert sick_pay(E2, START, END, years).details["percent"] == D(pct)


def test_r6_three_days_only_employer() -> None:
    r = sick_pay(E2, date(2026, 3, 2), date(2026, 3, 4), D(10))
    assert r.details["fund_part"] == D("0.00")
    assert r.details["employer_part"] == r.value


def test_r6_one_day() -> None:
    r = sick_pay(E2, date(2026, 3, 2), date(2026, 3, 2), D(10))
    assert r.details["days"] == 1
    assert r.value == D("3013.70")


def test_r6_year_cap() -> None:
    # 2024 base 2,225,000; 2025 - 2,759,000: earnings above it are capped
    r = sick_pay({2024: D(5000000), 2025: D(5000000)}, START, END, D(10))
    assert r.details["avg_daily"] == D("6827.40")
    assert r.value == D("68274.00")


def test_r6_min_from_mrot() -> None:
    r = sick_pay({2024: D(100000), 2025: D(100000)}, START, END, D(10))
    assert r.details["avg_daily"] == D("890.73")  # 27093 x 24 / 730
    assert r.details["min_applied"] is True


def test_r6_excluded_days_reduce_divisor() -> None:
    r = sick_pay(E2, START, END, D(10), excluded_days=30)
    assert r.details["avg_daily"] == D("3142.86")  # 2 200 000 / 700


def test_r6_short_stazh_note() -> None:
    r = sick_pay(E2, START, END, D("0.3"))
    assert any("6 мес" in n for n in r.notes)


def test_r6_bad_input() -> None:
    with pytest.raises(CalcError):
        sick_pay(E2, END, START, D(6))
    with pytest.raises(CalcError):
        sick_pay(E2, START, END, D(-1))
    with pytest.raises(CalcError):
        sick_pay({2024: D(1)}, START, END, D(6))
    with pytest.raises(CalcError):
        sick_pay({2020: D(1), 2021: D(1)}, START, END, D(6))
    with pytest.raises(CalcError):
        sick_pay({2024: D(-1), 2025: D(1)}, START, END, D(6))
    with pytest.raises(CalcError):
        sick_pay(E2, START, END, D(6), excluded_days=730)
    with pytest.raises(CalcError):  # no minimum wage/base for the sickness year
        sick_pay({2028: D(1), 2029: D(1)}, date(2030, 1, 10), date(2030, 1, 12), D(6))


def test_r6_leap_year_divisor_still_730() -> None:
    r = sick_pay({2023: D(730000), 2024: D(730000)}, date(2025, 2, 3), date(2025, 2, 7), D(10))
    assert r.details["avg_daily"] == D("2000.00")
