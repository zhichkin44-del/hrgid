"""10 контрольных расчётов (data/evals/calc.jsonl) — эталоны посчитаны вручную, до рубля."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hrgid.calc import overtime, sick, terms, vacation
from hrgid.calc.common import CalcResult

EVALS = Path(__file__).resolve().parent.parent / "data" / "evals" / "calc.jsonl"


def _d(v: Any) -> Any:
    if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
        return date.fromisoformat(v)
    return v


def run(case: dict[str, Any]) -> CalcResult:
    a = {k: _d(v) for k, v in case["input"].items()}
    rule = case["rule"]
    if rule == "R1":
        return vacation.vacation_pay(
            Decimal(a["earnings"]), a["start"], a["days"],
            excluded_days={(2026, 3): a["excluded_mar"]} if "excluded_mar" in a else None,
        )
    if rule == "R2":
        return vacation.leave_compensation(
            Decimal(a["earnings"]), a["dismissal"], Decimal(a["unused_days"]), hire_date=a["hire"]
        )
    if rule == "R3":
        return vacation.accrued_vacation(a["hire"], a["on"])
    if rule == "R4":
        days = [(_d(d), Decimal(h)) for d, h in a["days"]]
        return overtime.overtime_pay(Decimal(a["salary"]), Decimal(a["norm"]), days)
    if rule == "R5":
        return overtime.weekend_work_pay(
            a["date"], Decimal(a["hours"]), Decimal(a["salary"]), Decimal(a["norm"])
        )
    if rule == "R6":
        return sick.sick_pay(
            {int(y): Decimal(v) for y, v in a["earnings"].items()}, a["start"], a["end"],
            Decimal(a["stazh"]),
        )
    if rule == "R7":
        return terms.last_working_day(a["application"])
    if rule == "R8":
        return terms.payment_deadline("vacation", a["vacation_start"])
    if rule == "R9":
        return terms.probation_end(a["start"], a["months"], absence_days=a["absence_days"])
    raise AssertionError(rule)


def load() -> list[dict[str, Any]]:
    return [json.loads(x) for x in EVALS.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_ten_cases() -> None:
    assert len(load()) == 10


@pytest.mark.parametrize("case", load(), ids=lambda c: str(c["id"]))
def test_case_to_the_ruble(case: dict[str, Any]) -> None:
    r = run(case)
    exp = _d(case["expected"])
    if isinstance(r.value, Decimal):
        assert round(r.value) == round(Decimal(exp)), case["id"]
        assert r.value == Decimal(exp)
    else:
        assert r.value == exp
