"""Общие типы и округление расчётов. Только Decimal, никаких вызовов моделей."""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

Detail = Decimal | date | int | str | bool


class CalcError(ValueError):
    """Ошибка ввода или правила: сообщение показывается сотруднику."""


@dataclass(frozen=True)
class CalcResult:
    code: str  # R1..R9
    value: Decimal | date
    details: dict[str, Detail]
    formula: str
    substituted: str
    article: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def money(x: Decimal) -> Decimal:
    """Округление до копеек (математическое)."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def rubles(x: Decimal) -> Decimal:
    """Округление до рубля (НДФЛ): 50 копеек и больше — вверх."""
    return x.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def add_months(d: date, n: int) -> date:
    """Прибавить месяцы; если дня нет в месяце — последний день месяца."""
    idx = d.year * 12 + (d.month - 1) + n
    year, month = divmod(idx, 12)
    month += 1
    return date(year, month, min(d.day, _cal.monthrange(year, month)[1]))
