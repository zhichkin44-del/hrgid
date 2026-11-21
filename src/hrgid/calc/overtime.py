"""R4 сверхурочные, R5 работа в выходной."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from hrgid.calc.calendar import is_day_off
from hrgid.calc.common import CalcError, CalcResult, money
from hrgid.calc.rules import dparam, param, rule


def _rate(salary: Decimal, norm_hours: Decimal) -> Decimal:
    if salary <= 0:
        raise CalcError("Оклад должен быть больше нуля")
    if norm_hours <= 0:
        raise CalcError("Норма часов месяца должна быть больше нуля")
    return money(salary / norm_hours)


def overtime_pay(
    monthly_salary: Decimal,
    norm_hours: Decimal,
    days: Sequence[tuple[date, Decimal]],
    *,
    hours_before: Decimal = Decimal(0),
    time_off: bool = False,
) -> CalcResult:
    """R4. hours_before — сверхурочные часы с начала года (лимит 120 ч для множителя 1,5)."""
    rate = _rate(monthly_salary, norm_hours)
    if not days:
        raise CalcError("Не указано ни одного дня сверхурочной работы")
    first_hours = dparam("R4", "first_hours")
    limit = dparam("R4", "annual_limit_hours")
    m1, m2 = dparam("R4", "first_multiplier"), dparam("R4", "next_multiplier")
    cum = hours_before
    h15 = h2 = Decimal(0)
    for _, hours in days:
        if not 0 < hours <= int(param("R4", "max_hours_per_day")):
            raise CalcError("Часов за день должно быть больше 0 и не больше 24")
        n15 = max(Decimal(0), min(hours, first_hours, limit - cum))
        h15 += n15
        h2 += hours - n15
        cum += hours
    total = h15 + h2
    if time_off:
        pay = money(rate * total)
        text = f"{rate} x {total} ч (одинарная оплата) = {pay}; отдых не менее {total} ч"
    else:
        pay = money(rate * (m1 * h15 + m2 * h2))
        text = f"{rate} x ({m1} x {h15} + {m2} x {h2}) = {pay}"
    return CalcResult(
        code="R4",
        value=pay,
        details={
            "rate": rate,
            "hours_x1_5": h15,
            "hours_x2": h2,
            "hours_total": total,
            "rest_hours": total if time_off else Decimal(0),
        },
        formula=str(rule("R4")["formula"]),
        substituted=f"ставка {monthly_salary} / {norm_hours} = {rate}; {text}",
        article=str(rule("R4")["article"]),
        notes=(
            "Ст. 152 в ред. 25.05.2026: первые 2 часа дня — x1,5, далее — x2; "
            "со 121-го часа сверхурочных в году — x2 за каждый час",
        ),
    )


def weekend_work_pay(
    day: date,
    hours: Decimal,
    monthly_salary: Decimal,
    norm_hours: Decimal,
    *,
    variant: Literal["double", "rest_day"] = "double",
    salaried_within_norm: bool = False,
) -> CalcResult:
    """R5. double — не менее двойной; rest_day — одинарная оплата + день отдыха."""
    if variant not in ("double", "rest_day"):
        raise CalcError("Вариант: двойная оплата или одинарная с днём отдыха")
    if not is_day_off(day):
        raise CalcError("Это не выходной и не праздничный день по производственному календарю")
    if not 0 < hours <= int(param("R5", "max_hours_per_day")):
        raise CalcError("Часов должно быть больше 0 и не больше 24")
    rate = _rate(monthly_salary, norm_hours)
    single, double = dparam("R5", "single_multiplier"), dparam("R5", "double_multiplier")
    mult = single if variant == "rest_day" or salaried_within_norm else double
    pay = money(rate * hours * mult)
    rest_days = 1 if variant == "rest_day" else 0
    return CalcResult(
        code="R5",
        value=pay,
        details={"rate": rate, "multiplier": mult, "rest_days": rest_days},
        formula=str(rule("R5")["formula"]),
        substituted=(
            f"ставка {monthly_salary} / {norm_hours} = {rate}; {rate} x {hours} ч x {mult} = {pay}"
            + ("; плюс день отдыха (не оплачивается)" if rest_days else "")
        ),
        article=str(rule("R5")["article"]),
        notes=(
            ("Для работающих по окладу в пределах нормы — не менее одинарной ставки сверх оклада",)
            if salaried_within_norm
            else ()
        ),
    )
