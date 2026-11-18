"""Расчёты R1-R9 как инструменты модели (tool calling). Считает Python, модель только вызывает."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from hrgid import calc
from hrgid.calc.common import CalcError, CalcResult
from hrgid.llm.base import ToolSpec


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v).replace(",", ".").replace(" ", ""))
    except InvalidOperation as e:
        raise CalcError(f"Не число: {v!r}") from e


def _date(v: Any) -> date:
    try:
        return date.fromisoformat(str(v))
    except ValueError as e:
        raise CalcError(f"Дата должна быть в формате ГГГГ-ММ-ДД: {v!r}") from e


def _opt_date(v: Any) -> date | None:
    return None if v in (None, "") else _date(v)


def _vacation_pay(a: dict[str, Any]) -> CalcResult:
    return calc.vacation_pay(
        _d(a["earnings"]),
        _date(a["vacation_start"]),
        int(a["vacation_days"]),
        hire_date=_opt_date(a.get("hire_date")),
    )


def _leave_compensation(a: dict[str, Any]) -> CalcResult:
    return calc.leave_compensation(
        _d(a["earnings"]),
        _date(a["dismissal_date"]),
        _d(a["unused_days"]),
        hire_date=_opt_date(a.get("hire_date")),
    )


def _accrued_vacation(a: dict[str, Any]) -> CalcResult:
    return calc.accrued_vacation(
        _date(a["hire_date"]),
        _date(a["on_date"]),
        used_days=_d(a.get("used_days", 0)),
    )


def _sick_pay(a: dict[str, Any]) -> CalcResult:
    by_year = {int(y): _d(v) for y, v in dict(a["earnings_by_year"]).items()}
    return calc.sick_pay(by_year, _date(a["start"]), _date(a["end"]), _d(a["insurance_years"]))


def _overtime(a: dict[str, Any]) -> CalcResult:
    days = [(_date(x["date"]), _d(x["hours"])) for x in a["days"]]
    return calc.overtime_pay(
        _d(a["monthly_salary"]),
        _d(a["norm_hours"]),
        days,
        hours_before=_d(a.get("hours_before", 0)),
        time_off=bool(a.get("time_off", False)),
    )


def _weekend(a: dict[str, Any]) -> CalcResult:
    return calc.weekend_work_pay(
        _date(a["day"]), _d(a["hours"]), _d(a["monthly_salary"]), _d(a["norm_hours"])
    )


def _last_day(a: dict[str, Any]) -> CalcResult:
    nd = a.get("notice_days")
    return calc.last_working_day(
        _date(a["application_date"]), notice_days=None if nd in (None, "") else int(nd)
    )


def _deadline(a: dict[str, Any]) -> CalcResult:
    return calc.payment_deadline(str(a["kind"]), _date(a["ref_date"]))


def _probation(a: dict[str, Any]) -> CalcResult:
    return calc.probation_end(
        _date(a["start"]),
        int(a["months"]),
        senior=bool(a.get("senior", False)),
        absence_days=int(a.get("absence_days", 0)),
    )


def _spec(name: str, desc: str, props: dict[str, Any], required: list[str]) -> ToolSpec:
    return ToolSpec(name, desc, {"type": "object", "properties": props, "required": required})


_S = {"type": "string"}
_N = {"type": "number"}
_DATE = {"type": "string", "description": "дата ГГГГ-ММ-ДД"}

REGISTRY: dict[str, tuple[ToolSpec, Callable[[dict[str, Any]], CalcResult]]] = {
    "vacation_pay": (
        _spec(
            "vacation_pay",
            "R1. Отпускные: заработок за 12 месяцев, дата начала, число дней.",
            {
                "earnings": _N,
                "vacation_start": _DATE,
                "vacation_days": {"type": "integer"},
                "hire_date": _DATE,
            },
            ["earnings", "vacation_start", "vacation_days"],
        ),
        _vacation_pay,
    ),
    "leave_compensation": (
        _spec(
            "leave_compensation",
            "R2. Компенсация неиспользованного отпуска при увольнении.",
            {"earnings": _N, "dismissal_date": _DATE, "unused_days": _N, "hire_date": _DATE},
            ["earnings", "dismissal_date", "unused_days"],
        ),
        _leave_compensation,
    ),
    "accrued_vacation": (
        _spec(
            "accrued_vacation",
            "R3. Сколько дней отпуска накоплено на дату.",
            {"hire_date": _DATE, "on_date": _DATE, "used_days": _N},
            ["hire_date", "on_date"],
        ),
        _accrued_vacation,
    ),
    "sick_pay": (
        _spec(
            "sick_pay",
            "R4. Больничный: заработок по двум годам, даты, страховой стаж в годах.",
            {
                "earnings_by_year": {"type": "object", "description": "{год: сумма}"},
                "start": _DATE,
                "end": _DATE,
                "insurance_years": _N,
            },
            ["earnings_by_year", "start", "end", "insurance_years"],
        ),
        _sick_pay,
    ),
    "overtime_pay": (
        _spec(
            "overtime_pay",
            "R5. Оплата сверхурочных: оклад, норма часов месяца, дни с часами сверх нормы.",
            {
                "monthly_salary": _N,
                "norm_hours": _N,
                "days": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "[{date, hours}]",
                },
                "hours_before": _N,
                "time_off": {"type": "boolean"},
            },
            ["monthly_salary", "norm_hours", "days"],
        ),
        _overtime,
    ),
    "weekend_work_pay": (
        _spec(
            "weekend_work_pay",
            "R6. Оплата работы в выходной или праздник.",
            {"day": _DATE, "hours": _N, "monthly_salary": _N, "norm_hours": _N},
            ["day", "hours", "monthly_salary", "norm_hours"],
        ),
        _weekend,
    ),
    "last_working_day": (
        _spec(
            "last_working_day",
            "R7. Последний рабочий день после заявления об увольнении.",
            {"application_date": _DATE, "notice_days": {"type": "integer"}},
            ["application_date"],
        ),
        _last_day,
    ),
    "payment_deadline": (
        _spec(
            "payment_deadline",
            "R8. Срок выплаты: kind = vacation | final | salary.",
            {"kind": _S, "ref_date": _DATE},
            ["kind", "ref_date"],
        ),
        _deadline,
    ),
    "probation_end": (
        _spec(
            "probation_end",
            "R9. Дата окончания испытательного срока.",
            {
                "start": _DATE,
                "months": {"type": "integer"},
                "senior": {"type": "boolean"},
                "absence_days": {"type": "integer"},
            },
            ["start", "months"],
        ),
        _probation,
    ),
}

TOOLS: tuple[ToolSpec, ...] = tuple(spec for spec, _ in REGISTRY.values())


def run_tool(name: str, args: dict[str, Any]) -> CalcResult:
    """Выполнить расчёт. Ошибки ввода — CalcError с текстом для сотрудника."""
    if name not in REGISTRY:
        raise CalcError(f"Неизвестный расчёт: {name}")
    try:
        return REGISTRY[name][1](args)
    except KeyError as e:
        raise CalcError(f"Не хватает данных: {e.args[0]}") from e
    except (TypeError, ValueError) as e:
        if isinstance(e, CalcError):
            raise
        raise CalcError(f"Неверные данные: {e}") from e


def format_result(r: CalcResult) -> str:
    value = (
        r.value.isoformat() if isinstance(r.value, date) else f"{r.value:,.2f}".replace(",", " ")
    )
    lines = [f"Итог: {value}", f"Формула: {r.formula}", f"Подстановка: {r.substituted}",
             f"Основание: {r.article}"]  # fmt: skip
    lines += [f"Примечание: {n}" for n in r.notes]
    return "\n".join(lines)
