"""Parsing of employee input in the calculations: amounts, dates, hours, length of service. Error is
InputError with a hint."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}  # fmt: skip
MONTH_NAMES = {v: k for k, v in MONTHS.items()}
_MONTH_RE = "|".join(MONTHS)
_DMY = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?!\d)")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_TEXT = re.compile(rf"(?<!\d)(\d{{1,2}})\s+({_MONTH_RE})(?:\s+(\d{{4}}))?", re.I)


class InputError(ValueError):
    """Input not read; the message is shown to the employee (what is wrong + an example)."""


def _nearest(day: int, month: int, today: date) -> date:
    best: date | None = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            cand = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((cand - today).days) < abs((best - today).days):
            best = cand
    if best is None:
        raise InputError("Такой даты нет. Напишите, например: 14.12.2026 или «14 декабря».")
    return best


def _mk(day: int, month: int, year: int | None, today: date) -> date:
    if year is None:
        return _nearest(day, month, today)
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError as e:
        raise InputError("Такой даты нет. Напишите, например: 14.12.2026 или «14 декабря».") from e


def find_dates(text: str, today: date) -> list[date]:
    """All dates in the text, in order of appearance (YYYY-MM-DD, DD.MM[.YYYY], "14 December
    [2026]")."""
    found: list[tuple[int, date]] = []
    low = text.lower()
    for m in _ISO.finditer(low):
        found.append((m.start(), _mk(int(m[3]), int(m[2]), int(m[1]), today)))
    for m in _TEXT.finditer(low):
        y = int(m[3]) if m[3] else None
        found.append((m.start(), _mk(int(m[1]), MONTHS[m[2].lower()], y, today)))
    taken = [(m.start(), m.end()) for m in _ISO.finditer(low)]
    for m in _DMY.finditer(low):
        if any(a <= m.start() < b for a, b in taken):
            continue
        y = int(m[3]) if m[3] else None
        found.append((m.start(), _mk(int(m[1]), int(m[2]), y, today)))
    return [d for _, d in sorted(found)]


def parse_date(text: str, today: date) -> date:
    low = text.strip().lower()
    if low in ("сегодня", "today"):
        return today
    if low == "завтра":
        return today + timedelta(days=1)
    ds = find_dates(text, today)
    if not ds:
        raise InputError(
            "Не получилось прочитать дату.\nНапишите, например: 14.12.2026 или «14 декабря»."
        )
    return ds[0]


def parse_amount(text: str) -> Decimal:
    t = re.sub(r"(руб\w*|₽|р\.?)\s*$", "", text.strip().lower()).strip()
    if not re.fullmatch(r"-?\d[\d\s ]*([.,]\d{1,2})?", t):
        raise InputError(
            "Не получилось прочитать сумму.\n"
            "Нужно число — скажем, 1 020 000. Пишите без букв и знаков."
        )
    try:
        v = Decimal(re.sub(r"[\s ]", "", t).replace(",", "."))
    except InvalidOperation as e:  # pragma: no cover
        raise InputError("Не получилось прочитать сумму.") from e
    if v <= 0:
        raise InputError("Сумма должна быть больше нуля.\nНапишите, например: 1 020 000.")
    return v


def parse_number(text: str, *, what: str = "число") -> Decimal:
    t = text.strip().replace(",", ".")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(дн\w*|дня|день|мес\w*|ч\w*)?", t.lower())
    if not m:
        raise InputError(f"Не получилось прочитать {what}.\nНапишите число, например: 14.")
    return Decimal(m[1])


def parse_int(text: str, *, what: str = "число") -> int:
    v = parse_number(text, what=what)
    if v != v.to_integral_value():
        raise InputError(f"Нужно целое {what}, например: 14.")
    return int(v)


def parse_vacation(text: str, today: date) -> tuple[date, int]:
    """ "From 14 December, 14 days" -> (start date, days)."""
    ds = find_dates(text, today)
    if not ds:
        raise InputError(
            "Не получилось прочитать даты.\nНапишите, например: «С 14 декабря, 14 дней»."
        )
    low = text.lower()
    low = _TEXT.sub(" ", low)
    low = _DMY.sub(" ", low)
    low = _ISO.sub(" ", low)
    m = re.search(r"(\d+)\s*(?:календарн\w*\s*)?(?:дн|день|дня)", low)
    if not m:
        raise InputError("Не вижу число дней.\nНапишите, например: «С 14 декабря, 14 дней».")
    return ds[0], int(m[1])


def parse_overtime(text: str, today: date) -> list[tuple[date, Decimal]]:
    """ "12 November 3 hours, 17 November 2 hours" -> [(date, hours), ...]."""
    parts = [p for p in re.split(r"[;,\n]| и ", text) if p.strip()]
    days: list[tuple[date, Decimal]] = []
    for p in parts:
        ds = find_dates(p, today)
        rest = _DMY.sub(" ", _TEXT.sub(" ", _ISO.sub(" ", p.lower())))
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:ч\w*)?", rest)
        if not ds or not m:
            raise InputError(
                "Не получилось прочитать часы.\nНапишите по дням, например: «12 ноября 3 часа, "
                "17 ноября 2 часа»."
            )
        days.append((ds[0], Decimal(m[1].replace(",", "."))))
    if not days:
        raise InputError("Напишите по дням, например: «12 ноября 3 часа, 17 ноября 2 часа».")
    return days


def parse_years(text: str) -> Decimal:
    """ "6 years 4 months" -> 6.33; "6" -> 6; "6,5" -> 6.5."""
    low = text.lower().strip().replace(",", ".")
    y = re.search(r"(\d+(?:\.\d+)?)\s*(?:г|л)", low)
    mo = re.search(r"(\d+)\s*мес", low)
    if y or mo:
        v = Decimal(y[1]) if y else Decimal(0)
        if mo:
            v += (Decimal(mo[1]) / 12).quantize(Decimal("0.01"))
        return v
    if re.fullmatch(r"\d+(?:\.\d+)?", low):
        return Decimal(low)
    raise InputError("Не получилось прочитать стаж.\nНапишите, например: «6 лет 4 месяца» или 6.")


def parse_earnings_by_year(text: str) -> dict[int, Decimal]:
    """«2024 — 700 000, 2025 — 960 000» -> {2024: 700000, 2025: 960000}."""
    res: dict[int, Decimal] = {}
    for m in re.finditer(r"(20\d{2})\D{0,6}?(\d[\d\s ]*(?:[.,]\d{1,2})?)", text):
        res[int(m[1])] = parse_amount(m[2])
    if not res:
        raise InputError(
            "Не получилось прочитать заработок.\n"
            "Напишите по годам, например: «2024 — 700 000, 2025 — 960 000»."
        )
    return res


def parse_period(text: str, today: date) -> tuple[date, date]:
    """ "from 10 to 16 November", "10.11-16.11" -> (start, end); order as written."""
    ds = find_dates(text, today)
    if len(ds) == 2:
        return ds[0], ds[1]
    m = re.search(
        rf"(\d{{1,2}})\s*(?:по|-|–|—)\s*(\d{{1,2}})\s+({_MONTH_RE})(?:\s+(\d{{4}}))?", text.lower()
    )
    if m:
        mon = MONTHS[m[3]]
        y = int(m[4]) if m[4] else None
        return _mk(int(m[1]), mon, y, today), _mk(int(m[2]), mon, y, today)
    raise InputError("Не получилось прочитать даты.\nНапишите, например: «с 10 по 16 ноября».")
