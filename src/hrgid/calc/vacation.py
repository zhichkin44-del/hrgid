"""R1 vacation pay, R2 compensation on dismissal, R3 accumulated vacation days."""

from __future__ import annotations

import calendar as _cal
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal

from hrgid.calc.common import CalcError, CalcResult, Detail, add_months, money, rubles
from hrgid.calc.rules import dparam, param, rule


def _divisor(
    reference: date, hire_date: date | None, excluded: Mapping[tuple[int, int], int]
) -> Decimal:
    """Sum of "calendar days" in the settlement period: 29.3 for a full month, pro rata for a
    partial one."""
    avg_days = dparam("R1", "avg_days_per_month")
    total = Decimal(0)
    for k in range(1, 13):
        first = add_months(reference.replace(day=1), -k)
        dim = _cal.monthrange(first.year, first.month)[1]
        if hire_date is not None and hire_date > first.replace(day=dim):
            continue  # the month before hiring
        lost = excluded.get((first.year, first.month), 0)
        if lost < 0 or lost > dim:
            raise CalcError(f"Исключаемых дней в {first:%m.%Y}: {lost}, в месяце {dim}")
        if hire_date is not None and hire_date >= first:
            lost += (hire_date - first).days
        counted = dim - lost
        if counted <= 0:
            continue
        total += avg_days if lost == 0 else avg_days / dim * counted
    if total == 0:
        raise CalcError("В расчётном периоде нет отработанных месяцев — нужен другой порядок")
    return total


def _pay(
    code: str,
    earnings: Decimal,
    reference: date,
    days: Decimal,
    hire_date: date | None,
    excluded: Mapping[tuple[int, int], int] | None,
    title: str,
) -> CalcResult:
    if earnings <= 0:
        raise CalcError("Заработок за расчётный период должен быть больше нуля")
    exact = _divisor(reference, hire_date, excluded or {})
    divisor = exact.quantize(Decimal("0.0001"))  # for display; calculated on the exact value
    avg = money(earnings / exact)
    gross = money(avg * days)
    rate = dparam("R1", "ndfl_rate")
    ndfl = rubles(gross * rate)
    details: dict[str, Detail] = {
        "avg_daily": avg,
        "divisor": divisor,
        "days": days,
        "ndfl": ndfl,
        "net": gross - ndfl,
    }
    return CalcResult(
        code=code,
        value=gross,
        details=details,
        formula=str(rule(code)["formula"]),
        substituted=(
            f"{title}: {earnings} / {divisor} = {avg} в день; {avg} x {days} = {gross}; "
            f"НДФЛ {gross} x {rate} = {ndfl}; на руки {gross - ndfl}"
        ),
        article=str(rule(code)["article"]),
        notes=("Сумма до НДФЛ; НДФЛ показан отдельно по ставке 13 %",),
    )


def vacation_pay(
    earnings: Decimal,
    vacation_start: date,
    vacation_days: int,
    *,
    hire_date: date | None = None,
    excluded_days: Mapping[tuple[int, int], int] | None = None,
) -> CalcResult:
    """R1. earnings - accrued earnings for 12 months before the month the vacation starts."""
    if not 1 <= vacation_days <= int(param("R1", "max_vacation_days")):
        raise CalcError("Дней отпуска должно быть от 1 до 56")
    r = _pay(
        "R1",
        earnings,
        vacation_start,
        Decimal(vacation_days),
        hire_date,
        excluded_days,
        "Отпускные",
    )
    r.details["pay_deadline"] = vacation_start - timedelta(days=int(param("R1", "pay_before_days")))
    return r


def leave_compensation(
    earnings: Decimal,
    dismissal_date: date,
    unused_days: Decimal,
    *,
    hire_date: date | None = None,
    excluded_days: Mapping[tuple[int, int], int] | None = None,
) -> CalcResult:
    """R2. Compensation for all unused vacation days."""
    if unused_days < 0:
        raise CalcError("Неиспользованных дней не может быть меньше нуля")
    return _pay(
        "R2", earnings, dismissal_date, unused_days, hire_date, excluded_days, "Компенсация"
    )


def accrued_vacation(
    hire_date: date,
    on_date: date,
    *,
    annual_days: Decimal | None = None,
    used_days: Decimal = Decimal(0),
    excluded_days: int = 0,
) -> CalcResult:
    """R3. A remainder of the month of 15 days or more counts as a full month; fewer is
    discarded."""
    if on_date < hire_date:
        raise CalcError("Дата расчёта не может быть раньше даты приёма на работу")
    annual = annual_days if annual_days is not None else dparam("R3", "annual_days")
    end = on_date - timedelta(days=excluded_days)
    months = 0
    while add_months(hire_date, months + 1) - timedelta(days=1) <= end:
        months += 1
    leftover = max((end - add_months(hire_date, months)).days + 1, 0)
    if leftover >= int(param("R3", "round_up_from_days")):
        months += 1
    accrued = money(annual / 12 * months)
    balance = money(accrued - used_days)
    return CalcResult(
        code="R3",
        value=accrued,
        details={
            "months": months,
            "leftover_days": leftover,
            "used_days": used_days,
            "balance": balance,
            "can_take_now": on_date >= add_months(hire_date, 6),
        },
        formula=str(rule("R3")["formula"]),
        substituted=(
            f"{hire_date:%d.%m.%Y} - {on_date:%d.%m.%Y}: полных месяцев {months} "
            f"(остаток {leftover} дн.); {annual} / 12 x {months} = {accrued}; "
            f"использовано {used_days}; остаток {balance}"
        ),
        article=str(rule("R3")["article"]),
    )
