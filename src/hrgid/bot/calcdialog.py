"""Расчёты R1–R9 в боте пошагово (SPEC-09 §4.5, SCREENS §16): профиль -> вопрос о недостающем ->
итог с формулой, числами, статьёй и «до НДФЛ / на руки». Считает hrgid.calc, не модель."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from hrgid import calc
from hrgid.bot import parse
from hrgid.bot.models import Button, Employee
from hrgid.bot.parse import MONTH_NAMES, InputError
from hrgid.bot.texts import money
from hrgid.calc.calendar import month_norm_hours
from hrgid.calc.common import CalcError, CalcResult, add_months, rubles

Rows = list[list[Button]]

TITLES = {
    "R1": "Отпускные",
    "R2": "Компенсация при увольнении",
    "R3": "Накопленный отпуск",
    "R4": "Сверхурочные",
    "R5": "Работа в выходной",
    "R6": "Больничный",
    "R7": "Последний рабочий день",
    "R8": "Срок выплаты",
    "R9": "Испытательный срок",
}
ORDER = ["R1", "R6", "R3", "R2", "R4", "R5", "R7", "R8", "R9"]
NDFL = Decimal("0.13")


def b(text: str, data: str) -> Button:
    return Button(text=text, data=data)


CANCEL = [b("Отмена", "calc:cancel")]


@dataclass
class CalcReply:
    text: str
    rows: Rows = field(default_factory=list)
    done: bool = False
    summary: str = ""  # короткая запись для «Спросить HR»
    rate: bool = False  # показать 👍/👎
    cancelled: bool = False


@dataclass
class CalcState:
    code: str
    stage: str = "start"
    data: dict[str, Any] = field(default_factory=dict)


def picker_rows() -> Rows:
    rows: Rows = []
    row: list[Button] = []
    for c in ORDER:
        row.append(b(TITLES[c], f"calc:{c}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(CANCEL)
    return rows


PICKER_TEXT = "Что посчитать?"


def _dm(d: date) -> str:
    return f"{d:%d.%m.%Y}"


def profile_line(e: Employee) -> str:
    bits: list[str] = []
    if e.salary is not None:
        bits.append(f"оклад {money(e.salary, False)}")
    if e.hire_date:
        bits.append(f"принят(а) {_dm(e.hire_date)}")
    if e.schedule:
        bits.append(f"график {e.schedule}")
    if not bits or e.profile_date is None:
        return "В профиле нет данных для этого расчёта — спрошу сам."
    return (
        f"Беру ваши данные из профиля — данные на {_dm(e.profile_date)} "
        f"(дата последнего импорта): {' · '.join(bits)}."
    )


def _ndfl_block(gross: Decimal) -> str:
    tax = rubles(gross * NDFL)
    return f"НДФЛ 13 % — {money(tax, False)} · на руки {money(gross - tax)}"


def _footer(r: CalcResult, *, approx: bool = True) -> str:
    out = f"Основание: {r.article}."
    for n in r.notes:
        out += f"\n{n}."
    if approx:
        out += "\nРасчёт ориентировочный: точную сумму считает бухгалтерия."
    return out


def _after(rate: bool = False, summary: str = "") -> CalcReply:
    rows: Rows = [[b("Пересчитать", "calc:redo"), b("Спросить HR", "calc:hr")]]
    if rate:
        rows.append([b("👍", "calc:up"), b("👎", "calc:down")])
    rows.append([b("Другой расчёт", "calc:other")])
    return CalcReply("", rows, True, summary, rate)


def _done(text: str, summary: str, rate: bool = False) -> CalcReply:
    r = _after(rate, summary)
    r.text = text
    return r


def _dec(v: Decimal | date) -> Decimal:
    assert isinstance(v, Decimal)
    return v


def _ask(text: str, rows: Rows | None = None) -> CalcReply:
    return CalcReply(text, [*(rows or []), CANCEL])


def _money_result(title: str, r: CalcResult) -> str:
    gross = r.value
    assert isinstance(gross, Decimal)
    return f"{title}: {money(gross)} до НДФЛ\n{r.substituted}\n{_ndfl_block(gross)}\n"


# --- запуск ---------------------------------------------------------------------------------


def start(code: str, e: Employee, today: date) -> tuple[CalcState, CalcReply]:
    st = CalcState(code)
    head = f"{TITLES[code]}. {profile_line(e)}\n"
    if code == "R1":
        st.stage = "dates"
        return st, _ask(head + "С какого числа и на сколько дней отпуск?")
    if code == "R2":
        st.stage = "date"
        return st, _ask(head + "Какая дата увольнения?")
    if code == "R3":
        return _need_hire(st, e, "on_date", head, today)
    if code == "R4":
        return _need_salary(st, e, "days", head, today)
    if code == "R5":
        return _need_salary(st, e, "date", head, today)
    if code == "R6":
        return _r6_start(st, e, head)
    if code == "R7":
        st.stage = "date"
        return st, _ask(head + "Какого числа подано заявление об увольнении?")
    if code == "R8":
        st.stage = "kind"
        return st, _ask(
            "Срок выплаты. Какую выплату проверить?",
            [
                [b("Отпускные", "cs:vacation")],
                [b("Расчёт при увольнении", "cs:final")],
                [b("Зарплата", "cs:salary")],
            ],
        )
    st.stage = "start_date"
    return st, _ask(head + "С какого числа начинается испытательный срок?")


def _need_hire(
    st: CalcState, e: Employee, nxt: str, head: str, today: date
) -> tuple[CalcState, CalcReply]:
    if e.hire_date is None:
        st.stage = "hire"
        st.data["next"] = nxt
        return st, _ask(head + "Какая дата приёма на работу?")
    st.data["hire"] = e.hire_date
    return _ask_on_date(st, head)


def _ask_on_date(st: CalcState, head: str) -> tuple[CalcState, CalcReply]:
    st.stage = "on_date"
    return st, _ask(head + "На какую дату посчитать?", [[b("Сегодня", "cs:today")]])


def _need_salary(
    st: CalcState, e: Employee, nxt: str, head: str, today: date
) -> tuple[CalcState, CalcReply]:
    if e.salary is None:
        st.stage = "salary"
        st.data["next"] = nxt
        return st, _ask(head + "Какой оклад в месяц, ₽ до НДФЛ?")
    st.data["salary"] = e.salary
    return _enter_salary_stage(st, nxt, head)


def _enter_salary_stage(st: CalcState, nxt: str, head: str = "") -> tuple[CalcState, CalcReply]:
    st.stage = nxt
    if st.code == "R4":
        return st, _ask(
            head + "Напишите по дням, сколько часов переработки — скажем, «12 ноября 3 часа, "
            "17 ноября 2 часа»."
        )
    return st, _ask(head + "Какая дата? Работа в выходной или праздничный день.")


def _r6_start(st: CalcState, e: Employee, head: str) -> tuple[CalcState, CalcReply]:
    st.stage = "stazh"
    hire = f"принят(а) {_dm(e.hire_date)}.\n" if e.hire_date else ""
    rows: Rows = [[b("Верно", "cs:profile")]] if e.insurance_years is not None else []
    return st, _ask(
        "Больничный. "
        + (f"Данные из профиля: {hire}" if hire else "")
        + "Спрашиваю только даты, стаж и заработок. Диагноз и причина болезни мне не нужны — "
        "я их не спрашиваю и не храню.\n"
        + (
            f"Страховой стаж по профилю: {_years(e.insurance_years)}."
            if e.insurance_years is not None
            else "Какой страховой стаж всего, с прошлых мест работы? Например: «6 лет 4 месяца»."
        ),
        rows,
    )


def _years(v: Decimal) -> str:
    y = int(v)
    m = int((v - y) * 12 + Decimal("0.5"))
    return f"{y} л. {m} мес." if m else f"{y} л."


# --- шаги -----------------------------------------------------------------------------------


def advance(
    st: CalcState, e: Employee, today: date, text: str, *, callback: bool = False
) -> CalcReply:
    """Следующий шаг. Ошибка ввода -> тот же вопрос с пояснением (state не меняется)."""
    if text.strip().lower() in ("отмена", "/cancel", "calc:cancel"):
        return CalcReply("Отменено, ничего не сохранено.", cancelled=True, done=True)
    try:
        return _STEPS[st.code](st, e, today, text.strip())
    except (InputError, CalcError) as err:
        return _ask(f"{err}\n{_reprompt(st)}", _rows_for(st))


def _rows_for(st: CalcState) -> Rows:
    """Кнопки шага, которые нужно показать и при ошибке ввода."""
    if st.stage == "on_date":
        return [[b("Сегодня", "cs:today")]]
    if st.stage == "earn_confirm":
        return [
            [b("Да, верно", "cs:yes")],
            [b("Ввести свою сумму", "cs:own"), b("Были премии", "cs:bonus")],
        ]
    if st.stage == "variant":
        return [[b("Двойная оплата", "cs:double")], [b("Одинарная + день отдыха", "cs:rest_day")]]
    if st.stage == "kind":
        return [
            [b("Отпускные", "cs:vacation")],
            [b("Расчёт при увольнении", "cs:final")],
            [b("Зарплата", "cs:salary")],
        ]
    return []


def _reprompt(st: CalcState) -> str:
    prompts = {
        "dates": "С какого числа и на сколько дней отпуск?",
        "date": "Напишите дату, например: 14.12.2026.",
        "on_date": "На какую дату посчитать?",
        "own": "Напишите сумму одним числом, например: 1 020 000.",
        "days": "Напишите по дням, например: «12 ноября 3 часа, 17 ноября 2 часа».",
        "hours": "Сколько часов — число от 0 до 24.",
        "stazh": "Напишите стаж, например: «6 лет 4 месяца».",
        "period": "Напишите даты, например: «с 10 по 16 ноября».",
        "earn": "Напишите заработок по годам, например: «2024 — 700 000, 2025 — 960 000».",
        "months": "Сколько месяцев длится испытание?",
    }
    return prompts.get(st.stage, "Попробуйте ещё раз или нажмите «Отмена».")


def _earnings_prompt(st: CalcState, e: Employee, title_period: str) -> CalcReply:
    """Заработок за 12 месяцев: из импорта, предположение «оклад × 12» или свой ввод."""
    st.stage = "earn_confirm"
    if e.earnings_12m is not None:
        st.data["assumed"] = e.earnings_12m
        q = f"Заработок за период по импорту: {money(e.earnings_12m, False)}. Верно?"
    elif e.salary is not None:
        st.data["assumed"] = e.salary * 12
        q = (
            "В импорте нет заработка за этот период.\nЕсли оклад не менялся и премий не было, "
            f"он равен {money(e.salary, False)} × 12 = {money(e.salary * 12, False)}. Подставить?"
        )
    else:
        st.stage = "own"
        return _ask(
            title_period + "\nНапишите заработок за 12 месяцев до отпуска одним числом, в рублях, "
            "до вычета НДФЛ."
        )
    return _ask(
        f"{title_period}\n{q}",
        [
            [b("Да, верно", "cs:yes")],
            [b("Ввести свою сумму", "cs:own"), b("Были премии", "cs:bonus")],
        ],
    )


def _earnings_step(st: CalcState, e: Employee, text: str) -> CalcReply | Decimal:
    """Общий разбор ответа на вопрос о заработке; возвращает сумму либо следующий вопрос."""
    if st.stage == "earn_confirm":
        if text in ("cs:yes", "Да, верно"):
            return Decimal(st.data["assumed"])
        if text not in ("cs:own", "cs:bonus"):
            amount = parse.parse_amount(text)  # число вместо кнопки — принимаем как свою сумму
            st.data["own_sum"] = True
            return amount
        st.stage = "own"
        if text == "cs:bonus":
            return _ask("Напишите общий заработок за 12 месяцев с учётом премий, до вычета НДФЛ.")
        return _ask(
            "Напишите заработок за 12 месяцев до отпуска одним числом, в рублях, до вычета НДФЛ."
        )
    amount = parse.parse_amount(text)
    st.data["own_sum"] = True
    return amount


def _own_note(st: CalcState, amount: Decimal) -> str:
    if st.data.get("own_sum"):
        return (
            f"Принял: {money(amount, False)} — это значение только для этого расчёта, "
            "в профиль оно не сохраняется.\n"
        )
    return ""


def _period_text(vac_start: date) -> str:
    first = add_months(date(vac_start.year, vac_start.month, 1), -12)
    last = date(vac_start.year, vac_start.month, 1) - timedelta(days=1)
    return (
        f"Расчётный период — 12 месяцев до месяца отпуска: {_dm(first)} – {_dm(last)} "
        "(ст. 139 ТК РФ)."
    )


def _r1(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "dates":
        start_d, days = parse.parse_vacation(text, today)
        if start_d < today - timedelta(days=365):
            raise InputError("Дата начала отпуска не раньше чем год назад.")
        if not 1 <= days <= 56:
            raise InputError("Дней отпуска должно быть от 1 до 56.")
        st.data.update(start=start_d, days=days)
        end = start_d + timedelta(days=days - 1)
        head = (
            f"Отпуск {_dm(start_d)} – {_dm(end)}, {days} календарных дней.\n{_period_text(start_d)}"
        )
        return _earnings_prompt(st, e, head)
    got = _earnings_step(st, e, text)
    if isinstance(got, CalcReply):
        return got
    r = calc.vacation_pay(got, st.data["start"], int(st.data["days"]), hire_date=e.hire_date)
    dl = r.details.get("pay_deadline")
    when = (
        f"\nКогда заплатят. Закон — не позднее чем за 3 календарных дня до отпуска: "
        f"до {_dm(dl)} (ст. 136 ТК РФ).\n"
        if isinstance(dl, date)
        else "\n"
    )
    body = _own_note(st, got) + _money_result("Отпускные", r) + when.lstrip("\n") + _footer(r)
    return _done(
        body,
        f"Отпускные {_dm(st.data['start'])}, {st.data['days']} дн.: {money(_dec(r.value))}",
        True,
    )


def _r2(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "date":
        d = parse.parse_date(text, today)
        if d < today - timedelta(days=30):
            raise InputError("Дата увольнения не раньше чем 30 дней назад.")
        st.data["date"] = d
        if e.unused_vacation_days is not None:
            st.data["unused"] = e.unused_vacation_days
            return _r2_earn(st, e)
        st.stage = "unused"
        return _ask("Сколько неиспользованных дней отпуска? Например: 12.")
    if st.stage == "unused":
        v = parse.parse_number(text, what="число дней")
        if v < 0:
            raise InputError("Дней не может быть меньше нуля.")
        st.data["unused"] = v
        return _r2_earn(st, e)
    got = _earnings_step(st, e, text)
    if isinstance(got, CalcReply):
        return got
    r = calc.leave_compensation(
        got, st.data["date"], Decimal(st.data["unused"]), hire_date=e.hire_date
    )
    body = _own_note(st, got) + _money_result("Компенсация", r) + _footer(r)
    return _done(body, f"Компенсация при увольнении {_dm(st.data['date'])}: {money(_dec(r.value))}")


def _r2_earn(st: CalcState, e: Employee) -> CalcReply:
    head = f"Неиспользованных дней: {st.data['unused']}. Дата увольнения {_dm(st.data['date'])}."
    return _earnings_prompt(st, e, head)


def _r3(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "hire":
        d = parse.parse_date(text, today)
        st.data["hire"] = d
        return _ask_on_date(st, "")[1]
    on = today if text == "cs:today" else parse.parse_date(text, today)
    hire: date = st.data["hire"]
    if on < hire:
        raise InputError("Дата не раньше даты приёма на работу.")
    r = calc.accrued_vacation(hire, on, used_days=e.used_vacation_days)
    det = r.details
    body = (
        f"Накопленный отпуск на {_dm(on)}: {r.value} дн.\n{r.substituted}\n"
        f"Остаток к использованию: {det['balance']} дн.\n" + _footer(r, approx=False)
    )
    return _done(body, f"Накопленный отпуск на {_dm(on)}: {r.value} дн.")


def _r4(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "salary":
        st.data["salary"] = parse.parse_amount(text)
        return _enter_salary_stage(st, st.data["next"])[1]
    days = parse.parse_overtime(text, today)
    first = days[0][0]
    norm = month_norm_hours(first.year, first.month)
    r = calc.overtime_pay(Decimal(st.data["salary"]), norm, days, hours_before=e.overtime_ytd)
    d = r.details
    body = (
        _money_result("Сверхурочные", r)
        + f"Норма {MONTH_NAMES[first.month]} {first.year}: {norm} ч. "
        f"С множителем 1,5 — {d['hours_x1_5']} ч, с множителем 2 — {d['hours_x2']} ч.\n"
        "Первые 2 часа дня оплачиваются не менее чем в полуторном размере, дальше — не менее "
        "чем в двойном; полуторный размер действует в пределах 120 сверхурочных часов в год "
        "(ст. 152 ТК РФ в ред. 25.05.2026), сверх лимита — двойной. "
        f"Уже учтено часов с начала года: {e.overtime_ytd}.\n"
        "Вместо оплаты можно взять отгул — по согласию сторон.\n" + _footer(r)
    )
    return _done(body, f"Сверхурочные: {money(_dec(r.value))}")


def _r5(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "salary":
        st.data["salary"] = parse.parse_amount(text)
        return _enter_salary_stage(st, st.data["next"])[1]
    if st.stage == "date":
        d = parse.parse_date(text, today)
        from hrgid.calc.calendar import is_day_off

        if not is_day_off(d):
            raise InputError(
                "Это не выходной: по календарю в этот день рабочий. Назовите другую дату."
            )
        st.data["day"] = d
        st.stage = "hours"
        return _ask("Сколько часов вы работали? Число от 0 до 24.")
    if st.stage == "hours":
        h = parse.parse_number(text, what="часы")
        if not 0 < h <= 24:
            raise InputError("Часов должно быть больше 0 и не больше 24.")
        st.data["hours"] = h
        st.stage = "variant"
        return _ask(
            "Как компенсировать?",
            [
                [b("Двойная оплата", "cs:double")],
                [b("Одинарная + день отдыха", "cs:rest_day")],
            ],
        )
    variant: Literal["double", "rest_day"] = (
        "rest_day" if text in ("cs:rest_day", "Одинарная + день отдыха") else "double"
    )
    day: date = st.data["day"]
    norm = month_norm_hours(day.year, day.month)
    r = calc.weekend_work_pay(
        day, Decimal(st.data["hours"]), Decimal(st.data["salary"]), norm, variant=variant
    )
    body = _money_result("Работа в выходной", r) + _footer(r)
    return _done(body, f"Работа в выходной {_dm(day)}: {money(_dec(r.value))}")


def _r6(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "stazh":
        if text in ("", "cs:profile") and e.insurance_years is not None:
            st.data["stazh"] = e.insurance_years
        else:
            v = parse.parse_years(text)
            if v < 0:
                raise InputError("Стаж не может быть отрицательным.")
            st.data["stazh"] = v
        st.stage = "period"
        return _ask("Какие даты больничного? Например: «с 10 по 16 ноября».")
    if st.stage == "period":
        a, z = parse.parse_period(text, today)
        if z < a:
            raise InputError("Даты нужно по порядку: сначала начало, потом конец.")
        st.data.update(start=a, end=z)
        y1, y2 = a.year - 2, a.year - 1
        st.stage = "earn"
        hire = (
            f" (заработок {y1} считается с даты приёма {_dm(e.hire_date)})"
            if e.hire_date and e.hire_date.year >= y1
            else ""
        )
        return _ask(
            f"Какой заработок за {y1} и {y2} годы до вычета НДФЛ?{hire}\n"
            f"Например: «{y1} — 700 000, {y2} — 960 000»."
        )
    a, z = st.data["start"], st.data["end"]
    earn = parse.parse_earnings_by_year(text)
    r = calc.sick_pay(earn, a, z, Decimal(st.data["stazh"]))
    d = r.details
    emp_part, fund = d["employer_part"], d["fund_part"]
    assert isinstance(r.value, Decimal) and isinstance(emp_part, Decimal)
    assert isinstance(fund, Decimal)
    body = (
        _money_result("Больничный", r)
        + f"Стаж {_years(Decimal(st.data['stazh']))}; делитель 730 (ст. 14 255-ФЗ).\n"
        f"Кто платит. Первые дни — работодатель: {money(emp_part)}. Остальное — Социальный "
        f"фонд: {money(fund)} (ст. 3 255-ФЗ).\n" + _footer(r)
    )
    return _done(body, f"Больничный {_dm(a)} – {_dm(z)}: {money(_dec(r.value))}", True)


def _r7(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    d = parse.parse_date(text, today)
    if d < today - timedelta(days=60):
        raise InputError("Дата в далёком прошлом. Напишите дату подачи заявления ещё раз.")
    r = calc.last_working_day(d)
    assert isinstance(r.value, date)
    nxt = r.details.get("next_working_day")
    extra = (
        f"\nБлижайший рабочий день после этой даты: {_dm(nxt)}."
        if isinstance(nxt, date) and nxt != r.value
        else ""
    )
    body = f"Последний рабочий день: {_dm(r.value)}\n{r.substituted}{extra}\n" + _footer(
        r, approx=False
    )
    return _done(body, f"Последний рабочий день: {_dm(r.value)}")


def _r8(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "kind":
        names = {"cs:vacation": "vacation", "cs:final": "final", "cs:salary": "salary"}
        low = {"отпускные": "vacation", "расчёт при увольнении": "final", "зарплата": "salary"}
        kind = names.get(text) or low.get(text.lower())
        if kind is None:
            raise InputError("Выберите вид выплаты кнопкой.")
        st.data["kind"] = kind
        st.stage = "date"
        q = {
            "vacation": "С какого числа отпуск?",
            "final": "Какой последний рабочий день?",
            "salary": "Какая дата конца периода, за который платят?",
        }[kind]
        return _ask(q)
    d = parse.parse_date(text, today)
    r = calc.payment_deadline(str(st.data["kind"]), d)
    assert isinstance(r.value, date)
    body = f"Срок выплаты: не позднее {_dm(r.value)}\n{r.substituted}\n" + _footer(r, approx=False)
    return _done(body, f"Срок выплаты: {_dm(r.value)}")


def _r9(st: CalcState, e: Employee, today: date, text: str) -> CalcReply:
    if st.stage == "start_date":
        st.data["start"] = parse.parse_date(text, today)
        st.stage = "months"
        return _ask("На сколько месяцев установлен испытательный срок? Например: 3.")
    if st.stage == "months":
        m = parse.parse_int(text, what="число месяцев")
        calc.probation_end(st.data["start"], m)  # проверка предела по ст. 70 до следующего вопроса
        st.data["months"] = m
        st.stage = "absence"
        return _ask("Были ли дни болезни или отсутствия за время испытания? Напишите число или 0.")
    absence = parse.parse_int(text, what="число дней")
    r = calc.probation_end(st.data["start"], int(st.data["months"]), absence_days=absence)
    assert isinstance(r.value, date)
    body = (
        f"Испытательный срок заканчивается {_dm(r.value)}\n{r.substituted}\n"
        "Срок не может быть больше 3 месяцев (ст. 70 ТК РФ); до 6 месяцев — только для "
        "руководителей организации, их заместителей и главных бухгалтеров. Дни болезни сдвигают "
        "срок.\n" + _footer(r, approx=False)
    )
    return _done(body, f"Испытательный срок до {_dm(r.value)}")


_STEPS = {
    "R1": _r1, "R2": _r2, "R3": _r3, "R4": _r4, "R5": _r5,
    "R6": _r6, "R7": _r7, "R8": _r8, "R9": _r9,
}  # fmt: skip
