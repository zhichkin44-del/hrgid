"""Importing employees from a 1C:ZUP export (CSV/XLSX), П6.2-П6.5.

Steps: file -> column mapping -> preview (will be updated / new / with an error / not in the file)
-> summary. A repeated import updates the data of those already present (by full name) and offers to disable
those who are not in the new export.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from hrgid.bot.models import Employee

MAX_BYTES = 10 * 1024 * 1024
FIELDS: dict[str, str] = {
    "full_name": "ФИО",
    "hire_date": "Дата приёма",
    "position": "Должность",
    "schedule": "График",
    "salary": "Оклад",
    "group": "Группа",
    "earnings_12m": "Заработок за 12 месяцев",
}
REQUIRED = ("full_name", "hire_date")
_HINTS: dict[str, tuple[str, ...]] = {
    "full_name": ("фио", "сотрудник", "фамилия", "работник"),
    "hire_date": ("дата приема", "дата приёма", "принят", "прием на работу", "приём"),
    "position": ("должность", "профессия"),
    "schedule": ("график", "режим"),
    "salary": ("оклад", "тарифная ставка", "ставка"),
    "group": ("группа", "подразделение", "отдел"),
    "earnings_12m": ("заработок", "доход за 12", "за 12 мес"),
}


class ImportError_(ValueError):
    """The file was not accepted (format, or no "Full name" column)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower().replace("ё", "е"))


def read_table(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    """Headers and rows as text. Supports CSV (; or ,) and XLSX."""
    if len(data) > MAX_BYTES:
        raise ImportError_("size", "Файл больше 10 МБ.")
    name = filename.lower()
    if name.endswith(".xlsx"):
        return _xlsx(data)
    if name.endswith((".csv", ".txt")):
        return _csv(data)
    raise ImportError_("format", "Нужен файл CSV или XLSX из 1С:ЗУП.")


def _csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_("format", "Не удалось прочитать кодировку файла.")
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    rows = [r for r in rows if any(r)]
    if not rows:
        raise ImportError_("empty", "В файле нет строк.")
    return rows[0], rows[1:]


def _xlsx(data: bytes) -> tuple[list[str], list[list[str]]]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ImportError_("format", "Файл XLSX повреждён или защищён паролем.") from e
    ws = wb.active
    if ws is None:
        raise ImportError_("empty", "В файле нет листов.")
    rows: list[list[str]] = []
    for r in ws.iter_rows(values_only=True):
        cells = [_cell(c) for c in r]
        if any(cells):
            rows.append(cells)
    if not rows:
        raise ImportError_("empty", "В файле нет строк.")
    return rows[0], rows[1:]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def auto_map(headers: list[str]) -> dict[str, int]:
    """Columns by name (case-insensitive); whatever is not found is chosen by a person (П6.3)."""
    found: dict[str, int] = {}
    low = [h.lower().replace("ё", "е") for h in headers]
    for field_, hints in _HINTS.items():
        for i, h in enumerate(low):
            if i in found.values():
                continue
            if any(x.replace("ё", "е") in h for x in hints):
                found[field_] = i
                break
    return found


def parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(s: str) -> Decimal | None:
    t = re.sub(r"[\s ₽руб.р]+$", "", s.strip()).replace(" ", "").replace(" ", "")
    t = t.replace(",", ".")
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return v if v >= 0 else None


@dataclass
class RowError:
    line: int
    who: str
    problem: str


@dataclass
class Parsed:
    full_name: str
    hire_date: date
    position: str = ""
    schedule: str = ""
    salary: Decimal | None = None
    group: str = ""
    earnings_12m: Decimal | None = None


@dataclass
class Plan:
    new: list[Parsed] = field(default_factory=list)
    update: list[tuple[Employee, Parsed]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    missing: list[Employee] = field(default_factory=list)


def build_plan(rows: list[list[str]], mapping: dict[str, int], existing: list[Employee]) -> Plan:
    plan = Plan()
    by_name = {norm_name(e.full_name): e for e in existing}
    seen: set[str] = set()
    touched: set[str] = set()

    def col(r: list[str], key: str) -> str:
        i = mapping.get(key)
        return r[i].strip() if i is not None and i < len(r) else ""

    for line, r in enumerate(rows, start=2):
        name = col(r, "full_name")
        who = name or f"строка {line}"
        if not name:
            plan.errors.append(RowError(line, who, "Нет ФИО"))
            continue
        if norm_name(name) in seen:
            plan.errors.append(RowError(line, who, "ФИО повторяется в файле"))
            continue
        hd = parse_date(col(r, "hire_date"))
        if hd is None:
            plan.errors.append(
                RowError(line, who, "Дата приёма не прочитана (нужна дата вида 31.12.2025)")
            )
            continue
        salary = col(r, "salary")
        earn = col(r, "earnings_12m")
        sal_v = parse_money(salary) if salary else None
        earn_v = parse_money(earn) if earn else None
        if salary and sal_v is None:
            plan.errors.append(RowError(line, who, "Оклад — не число или меньше нуля"))
            continue
        if earn and earn_v is None:
            plan.errors.append(RowError(line, who, "Заработок — не число или меньше нуля"))
            continue
        seen.add(norm_name(name))
        p = Parsed(name, hd, col(r, "position"), col(r, "schedule"), sal_v, col(r, "group"), earn_v)
        ex = by_name.get(norm_name(name))
        if ex is None:
            plan.new.append(p)
        else:
            plan.update.append((ex, p))
            touched.add(ex.id)
    plan.missing = [e for e in existing if e.id not in touched and e.status == "active"]
    return plan
