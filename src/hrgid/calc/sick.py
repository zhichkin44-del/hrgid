"""R6 sick-leave benefit (Federal Law 255-FZ)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from hrgid.calc.common import CalcError, CalcResult, money
from hrgid.calc.rules import dparam, param, rule, table


def sick_pay(
    earnings_by_year: Mapping[int, Decimal],
    start: date,
    end: date,
    insurance_years: Decimal,
    *,
    excluded_days: int = 0,
) -> CalcResult:
    """earnings_by_year - earnings for the two calendar years preceding the year of illness."""
    if end < start:
        raise CalcError("Дата окончания болезни раньше даты начала")
    if insurance_years < 0:
        raise CalcError("Страховой стаж не может быть отрицательным")
    years = (start.year - 2, start.year - 1)
    if set(earnings_by_year) != set(years):
        raise CalcError(f"Нужен заработок за {years[0]} и {years[1]} годы")
    if any(v < 0 for v in earnings_by_year.values()):
        raise CalcError("Заработок не может быть отрицательным")
    limits = table("R6", "base_limits")
    mrot_by_year = table("R6", "mrot")
    if any(y not in limits for y in years) or start.year not in mrot_by_year:
        raise CalcError("Нет базы или МРОТ для этих лет — обновите labor_rules.yaml")
    divisor = int(param("R6", "divisor"))
    if not 0 <= excluded_days < divisor:
        raise CalcError("Исключаемых дней должно быть от 0 до 729")

    capped = sum((min(earnings_by_year[y], limits[y]) for y in years), Decimal(0))
    cap_daily = sum((limits[y] for y in years), Decimal(0)) / divisor
    avg = min(capped / (divisor - excluded_days), cap_daily)
    min_daily = mrot_by_year[start.year] * int(param("R6", "min_months_billing")) / divisor
    min_applied = avg < min_daily
    avg = money(max(avg, min_daily))

    if insurance_years < int(param("R6", "stazh_step_1_years")):
        pct = dparam("R6", "pct_stazh_lt_5")
    elif insurance_years < int(param("R6", "stazh_step_2_years")):
        pct = dparam("R6", "pct_stazh_5_to_8")
    else:
        pct = dparam("R6", "pct_stazh_ge_8")
    daily = money(avg * pct)
    days = (end - start).days + 1
    total = money(daily * days)
    employer_days = min(int(param("R6", "employer_days")), days)
    employer = money(daily * employer_days)

    notes = [str(rule("R6")["verified_note"])]
    if insurance_years < Decimal("0.5"):
        notes.append("Стаж менее 6 мес.: пособие не больше МРОТ за месяц — расчёт не учитывает")
    return CalcResult(
        code="R6",
        value=total,
        details={
            "avg_daily": avg,
            "percent": pct,
            "daily": daily,
            "days": days,
            "employer_part": employer,
            "fund_part": total - employer,
            "min_applied": min_applied,
        },
        formula=str(rule("R6")["formula"]),
        substituted=(
            f"заработок с учётом базы {capped} / {divisor - excluded_days} = {avg} в день; "
            f"{avg} x {pct} = {daily}; {daily} x {days} дн. = {total}; "
            f"работодатель {employer_days} дн. = {employer}, СФР = {total - employer}"
        ),
        article=str(rule("R6")["article"]),
        notes=tuple(notes),
    )
