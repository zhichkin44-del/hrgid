"""Производственный календарь РФ из data/calendar/<год>.json."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Any

from hrgid.calc.common import ROOT, CalcError


@lru_cache(maxsize=8)
def _year(year: int) -> dict[str, Any]:
    path = ROOT / "data" / "calendar" / f"{year}.json"
    if not path.exists():
        raise CalcError(f"Нет производственного календаря на {year} год")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def is_day_off(d: date) -> bool:
    """Выходной или нерабочий праздничный день по календарю."""
    return d.isoformat() in set(_year(d.year)["non_working_days"])


def working_days_in_month(year: int, month: int) -> int:
    return int(_year(year)["months"][str(month)]["working_days"])


def month_norm_hours(year: int, month: int) -> Decimal:
    """Норма рабочего времени месяца при 40-часовой неделе (с учётом сокращённых дней)."""
    return Decimal(int(_year(year)["months"][str(month)]["hours"]))
