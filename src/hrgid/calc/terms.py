"""R7 последний рабочий день, R8 срок выплаты, R9 испытательный срок."""

from __future__ import annotations

from datetime import date, timedelta

from hrgid.calc.calendar import is_day_off
from hrgid.calc.common import CalcError, CalcResult, add_months
from hrgid.calc.rules import param, rule


def _off(d: date) -> bool:
    try:
        return is_day_off(d)
    except CalcError:
        return False  # календаря на этот год нет — выходные не проверяются


def last_working_day(application_date: date, *, notice_days: int | None = None) -> CalcResult:
    """R7. Срок предупреждения идёт со следующего дня после заявления."""
    days = notice_days if notice_days is not None else int(param("R7", "notice_days"))
    if days < 1:
        raise CalcError("Срок предупреждения — не меньше одного дня")
    last = application_date + timedelta(days=days)
    nxt = last
    while _off(nxt):
        nxt += timedelta(days=1)
    notes: list[str] = []
    if nxt != last:
        notes.append(
            "Последний день выпал на выходной: по ст. 14 ТК срок может закончиться "
            "в ближайший рабочий день"
        )
    return CalcResult(
        code="R7",
        value=last,
        details={"next_working_day": nxt, "notice_days": days},
        formula=str(rule("R7")["formula"]),
        substituted=f"{application_date:%d.%m.%Y} + {days} дн. = {last:%d.%m.%Y}",
        article=str(rule("R7")["article"]),
        notes=tuple(notes),
    )


def payment_deadline(kind: str, ref_date: date) -> CalcResult:
    """R8. vacation: начало отпуска; final: последний рабочий день; salary: конец периода."""
    notes: list[str] = []
    if kind == "vacation":
        value = ref_date - timedelta(days=int(param("R8", "vacation_before_days")))
        text = f"{ref_date:%d.%m.%Y} - 3 дня = {value:%d.%m.%Y}"
        article = "ст. 136 ТК РФ"
        notes.append("Не позднее этой даты; выплатить можно раньше")
    elif kind == "final":
        value = ref_date
        text = f"в последний рабочий день {value:%d.%m.%Y}"
        article = "ст. 140 ТК РФ"
        notes.append("Если работник не работал в этот день — на следующий день после требования")
    elif kind == "salary":
        nominal = ref_date + timedelta(days=int(param("R8", "salary_after_days")))
        value = nominal
        while _off(value):
            value -= timedelta(days=1)
        text = f"{ref_date:%d.%m.%Y} + 15 дн. = {nominal:%d.%m.%Y}"
        if value != nominal:
            text += f"; выходной — выплата накануне, {value:%d.%m.%Y}"
        article = "ст. 136 ТК РФ"
    else:
        raise CalcError("Вид выплаты: отпускные, окончательный расчёт или зарплата")
    return CalcResult(
        code="R8",
        value=value,
        details={"kind": kind},
        formula=str(rule("R8")["formula"]),
        substituted=text,
        article=article,
        notes=tuple(notes),
    )


def probation_end(
    start: date,
    months: int,
    *,
    senior: bool = False,
    contract_months: int | None = None,
    absence_days: int = 0,
    weeks: int | None = None,
) -> CalcResult:
    """R9. senior — руководитель организации, заместитель, главбух (до 6 мес.)."""
    if absence_days < 0:
        raise CalcError("Дни отсутствия не могут быть отрицательными")
    shift = timedelta(days=absence_days - 1)
    if contract_months is not None and contract_months < 2:
        raise CalcError(
            "При договоре на срок до двух месяцев испытание не устанавливается (ст. 70 ТК)"
        )
    if contract_months is not None and contract_months <= 6:
        max_weeks = int(param("R9", "short_contract_weeks"))
        if months != 0 or weeks is None or not 1 <= weeks <= max_weeks:
            raise CalcError(
                "При договоре на срок от двух до шести месяцев испытание — не более двух недель"
            )
        end = start + timedelta(weeks=weeks) + shift
        span = f"{weeks} нед."
    else:
        limit = int(param("R9", "max_months_senior" if senior else "max_months"))
        if not 1 <= months <= limit:
            hint = (
                ""
                if senior
                else "; до 6 месяцев — только руководителям, их заместителям и главбухам"
            )
            raise CalcError(
                f"Срок испытания — не более {limit} месяцев по ст. 70 ТК и не меньше 1{hint}"
            )
        end = add_months(start, months) + shift
        span = f"{months} мес."
    return CalcResult(
        code="R9",
        value=end,
        details={"absence_days": absence_days},
        formula=str(rule("R9")["formula"]),
        substituted=(
            f"{start:%d.%m.%Y} + {span} - 1 день + {absence_days} дн. отсутствия = {end:%d.%m.%Y}"
        ),
        article=str(rule("R9")["article"]),
        notes=("Испытание не устанавливается для беременных, несовершеннолетних и др. (ст. 70)",),
    )
