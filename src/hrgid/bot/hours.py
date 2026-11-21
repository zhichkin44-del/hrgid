"""HR working hours and SLA in working hours (SPEC-09 §4.6, П9)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

from hrgid.bot.models import CompanySettings
from hrgid.calc.calendar import is_day_off
from hrgid.calc.common import CalcError


def tz(s: CompanySettings) -> timezone:
    return timezone(timedelta(hours=s.utc_offset))


def to_local(now: datetime, s: CompanySettings) -> datetime:
    return now.astimezone(tz(s))


def to_utc(local: datetime) -> datetime:
    return local.astimezone(UTC)


def is_workday(d: date, s: CompanySettings) -> bool:
    if d.weekday() not in s.work_weekdays:
        return False
    try:
        return not is_day_off(d)
    except CalcError:  # no calendar for this year, so count by weekdays
        return True


def _at(d: date, t: time, s: CompanySettings) -> datetime:
    return datetime.combine(d, t, tzinfo=tz(s))


def is_open(now: datetime, s: CompanySettings) -> bool:
    loc = to_local(now, s)
    return is_workday(loc.date(), s) and s.hours_start <= loc.time() < s.hours_end


def next_open(now: datetime, s: CompanySettings) -> datetime:
    """The nearest start of working hours (if open now, then now)."""
    loc = to_local(now, s)
    d = loc.date()
    for _ in range(400):
        if is_workday(d, s):
            start, end = _at(d, s.hours_start, s), _at(d, s.hours_end, s)
            if loc < start:
                return to_utc(start)
            if loc < end:
                return to_utc(loc)
        d += timedelta(days=1)
        loc = _at(d, time(0, 0), s)
    raise ValueError("нет рабочих дней в настройках")


def add_working_hours(start: datetime, hours: float, s: CompanySettings) -> datetime:
    """Deadline in `hours` working hours; non-working time is not counted."""
    cur = to_local(next_open(start, s), s)
    left = timedelta(hours=hours)
    for _ in range(2000):
        end = _at(cur.date(), s.hours_end, s)
        room = end - cur
        if left <= room:
            return to_utc(cur + left)
        left -= room
        cur = to_local(next_open(end, s), s)
    raise ValueError("не удалось посчитать срок")


def _day_word(now: datetime, when: datetime, s: CompanySettings) -> str:
    delta = (to_local(when, s).date() - to_local(now, s).date()).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    return f"{to_local(when, s):%d.%m}"


def deadline_phrase(now: datetime, due: datetime, s: CompanySettings) -> str:
    """ "today until 18:00", "tomorrow until 10:40", "20.11 until 12:00"."""
    return f"{_day_word(now, due, s)} до {to_local(due, s):%H:%M}"


def open_phrase(now: datetime, s: CompanySettings) -> str:
    """ "tomorrow from 10:00" is when HR is available again."""
    nxt = next_open(now, s)
    return f"{_day_word(now, nxt, s)} с {to_local(nxt, s):%H:%M}"


def hours_range(s: CompanySettings) -> str:
    days = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}
    wd = sorted(s.work_weekdays)
    span = f"{days[wd[0]]}–{days[wd[-1]]}" if len(wd) > 1 else days[wd[0]]
    return f"{span} с {s.hours_start:%H:%M} до {s.hours_end:%H:%M} ({s.tz_label})"
