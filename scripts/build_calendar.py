"""Builds data/calendar/<year>.json from the rules of the Russian production calendar.

Source of the rules: ConsultantPlus, Russian production calendar (consultant.ru/law/ref/calendar);
2025 - Government of the Russian Federation resolution No. 1335 of 04.10.2024, 2026 - No. 1466 of 24.09.2025.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "calendar"

# (month, day) of fixed public holidays under Art. 112 of the Labor Code; 1-8 January are holidays
FIXED = [(1, d) for d in range(1, 9)] + [(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)]

YEARS: dict[int, dict[str, object]] = {
    2025: {
        "source": "Постановление Правительства РФ от 04.10.2024 N 1335; consultant.ru",
        "moved_off": [(5, 2), (12, 31), (5, 8), (6, 13), (11, 3)],
        "working_weekends": [(11, 1)],
        "short_days": [(3, 7), (4, 30), (6, 11), (11, 1)],
    },
    2026: {
        "source": "Постановление Правительства РФ от 24.09.2025 N 1466; consultant.ru",
        "moved_off": [(1, 9), (12, 31), (3, 9), (5, 11)],
        "working_weekends": [],
        "short_days": [(4, 30), (5, 8), (6, 11), (11, 3)],
    },
}


def build(year: int) -> dict[str, object]:
    cfg = YEARS[year]
    short = {date(year, m, d) for m, d in cfg["short_days"]}  # type: ignore[attr-defined]
    work_we = {date(year, m, d) for m, d in cfg["working_weekends"]}  # type: ignore[attr-defined]
    holidays = {date(year, m, d) for m, d in FIXED}
    holidays |= {date(year, m, d) for m, d in cfg["moved_off"]}  # type: ignore[attr-defined]
    off: list[str] = []
    months: dict[str, dict[str, object]] = {}
    day = date(year, 1, 1)
    counts = {m: [0, 0] for m in range(1, 13)}
    while day.year == year:
        is_off = (day.weekday() >= 5 and day not in work_we) or day in holidays
        if is_off:
            off.append(day.isoformat())
        else:
            counts[day.month][0] += 1
            counts[day.month][1] += 8 - (1 if day in short else 0)
        day += timedelta(days=1)
    for m, (wd, hours) in counts.items():
        months[str(m)] = {"working_days": wd, "hours": hours}
    return {
        "year": year,
        "source": cfg["source"],
        "checked_on": "2026-11-18",
        "non_working_days": off,
        "short_days": sorted(d.isoformat() for d in short),
        "months": months,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        (OUT / f"{year}.json").write_text(
            json.dumps(build(year), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
