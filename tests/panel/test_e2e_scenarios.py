"""Six end-to-end SPEC-09 §17 scenarios: panel + bot on shared storage (Fake model).

The Playwright version of the same steps is tests/panel/test_browser_e2e.py (marker browser, in CI);
here the panel is checked through the FastAPI TestClient: HTML responses and their fragments.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any

from hrgid.bot.engine import BotEngine
from hrgid.bot.models import Out
from hrgid.docs.provider import DocRulesProvider
from hrgid.flow.graph import AskRequest, FlowAnswer, Source
from hrgid.panel.ops import BudgetGatedAnswerer

from .conftest import ADMIN, CO, HR, Env, make_env
from .seed import RULES, docx_bytes


class Pipe:
    """The stage 2 flow is replaced by a script: a question word -> a ready answer."""

    def __init__(self) -> None:
        self.script: list[tuple[str, FlowAnswer]] = []
        self.calls: list[AskRequest] = []

    def on(self, word: str, ans: FlowAnswer) -> None:
        self.script.append((word, ans))

    def ask(self, req: AskRequest) -> FlowAnswer:
        self.calls.append(req)
        for word, ans in self.script:
            if word in req.question:
                return ans
        return FlowAnswer("no_answer", "")


class Bot:
    def __init__(self, env: Env, pipe: Pipe, *, gated: bool = False) -> None:
        d = env.deps
        answerer: Any = BudgetGatedAnswerer(pipe, d.ops, env.clock) if gated else pipe
        rules = DocRulesProvider(d.docs, lambda: env.clock.now.date())
        self.env = env
        self.engine = BotEngine(
            d.repo, d.bot_auth, d.desk, d.kb, answerer, rules, default_company=CO
        )

    def say(self, tg: int, text: str) -> list[Out]:
        return [o for o in self.engine.on_message(tg, text, self.env.clock.now) if o.chat_id == tg]

    def tap(self, tg: int, last: list[Out], label: str) -> list[Out]:
        for out in last:
            for row in out.rows:
                for b in row:
                    if b.text == label:
                        outs = self.engine.on_callback(tg, b.data, self.env.clock.now)
                        return [o for o in outs if o.chat_id == tg]
        raise AssertionError(f"нет кнопки «{label}»")

    def login(self, tg: int, code: str) -> list[Out]:
        out = self.say(tg, code)
        out = self.tap(tg, out, "Согласен")
        assert "Согласие записано" in out[-1].text
        return out

    def push(self, tg: int) -> list[Out]:
        got: list[Out] = []

        def send(o: Out) -> bool:
            if o.chat_id == tg:
                got.append(o)
            return True

        self.engine.flush_outbox(send)
        return got


def text_of(outs: list[Out]) -> str:
    return "\n".join(o.text for o in outs)


def approve(env: Env, doc: str, number: int, no: str = "1", date_s: str = "01.11.2026") -> None:
    r = env.login(ADMIN).post(
        f"/docs/{doc}/{number}/order",
        data={"order_no": no, "order_date": date_s, "effective_from": date_s},
    )
    assert "Приказ записан" in r.text


def upload_rules(env: Env, lines: list[str] = RULES) -> str:
    r = env.login(HR).post(
        "/docs/upload",
        data={"title": "Правила студии"},
        files={"file": ("rules.docx", docx_bytes(lines), "application/octet-stream")},
    )
    assert "прочитано" in r.text
    return str(env.deps.docs.documents(CO)[0]["doc_id"])


STAFF_CSV = (
    "ФИО;Дата приёма;Должность;График;Оклад;Группа\n"
    "Козлов Дмитрий;03.06.2023;разработчик;5/2;150000;Разработка\n"
    "Лебедева Анастасия;15.08.2022;менеджер;5/2;120000;Менеджеры\n"
    "Никитин Сергей;20.01.2023;монтажёр;5/2;90000;Монтаж\n"
).encode()


def import_staff(env: Env) -> dict[str, str]:
    """Imports 3 employees, mass issue of codes CSV -> {full name: code}."""
    adm = env.login(ADMIN)
    r = adm.post("/staff/import", files={"file": ("zup.csv", STAFF_CSV, "text/csv")})
    token = re.search(r"/staff/import/(\w+)/map", str(r.url)).group(1)  # type: ignore[union-attr]
    r = adm.post(f"/staff/import/{token}/apply", data={})
    assert "Новых сотрудников" in r.text
    r = adm.post("/staff/codes", data={"who": "new", "fmt": "csv"})
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿")), delimiter=";"))
    return {row[0]: row[2] for row in rows[1:]}


# ---------------------------------------------------------------------------------------
# 1. New company: administrator -> document -> Labor Code check -> import 3 employees ->
#    question in Telegram -> answer
# ---------------------------------------------------------------------------------------


def test_scenario_1_new_company_to_first_answer() -> None:
    env = make_env()
    adm = env.login(ADMIN)
    assert "Начало работы" in adm.get("/").text  # new company: four steps
    doc = upload_rules(env)
    approve(env, doc, 1)
    r = adm.post(f"/check/{doc}/1/run")
    assert (
        "Проверка готова" in r.text and "ст. 136" in r.text
    )  # a deliberately introduced violation is found
    codes = import_staff(env)
    assert set(codes) == {"Козлов Дмитрий", "Лебедева Анастасия", "Никитин Сергей"}
    assert all(c.startswith("ЖС-") for c in codes.values())
    pipe = Pipe()
    pipe.on(
        "отпуск",
        FlowAnswer(
            "answered",
            "В студии отпуск — 31 календарный день.",
            "rules",
            [
                Source(
                    1, "Правила студии, п. 2.1", "2.1", "продолжительностью 31 календарный день", CO
                )
            ],
        ),
    )
    bot = Bot(env, pipe)
    bot.login(7001, codes["Козлов Дмитрий"])
    out = bot.say(7001, "Сколько дней отпуска мне положено?")
    assert "31 календарный день" in text_of(out) and "Правила студии, п. 2.1" in text_of(out)
    rules = bot.tap(7001, bot.say(7001, "Правила компании"), "Правила студии")
    assert "31 календарный день" in text_of(rules) and "приказ № 1" in text_of(rules)
    # in the panel the employee became "Connected", consent is recorded
    assert "Подключён" in adm.get("/staff").text
    kozlov = [e for e in env.deps.repo.employees(CO) if e.full_name == "Козлов Дмитрий"][0]
    assert kozlov.consent_at is not None and kozlov.telegram_id == 7001
    assert "Начало работы" not in adm.get("/").text


# ---------------------------------------------------------------------------------------
# 2. Question with no answer -> П1 -> П2 HR reply -> employee receives it -> "to the base" -> the same question
#    from another employee is answered from the base
# ---------------------------------------------------------------------------------------


def test_scenario_2_unanswered_to_hr_answer_to_kb() -> None:
    env = make_env()
    codes_src = env.deps.staff
    for name in ("Козлов Дмитрий", "Лебедева Анастасия"):
        codes_src.invite(CO, name, "01.01.2024", env.clock.now)
    codes = {
        e.full_name: env.deps.bot_auth.issue_code(CO, e.id, env.clock.now)
        for e in env.deps.repo.employees(CO)
    }
    pipe = Pipe()
    bot = Bot(env, pipe)
    bot.login(7001, codes["Козлов Дмитрий"])
    bot.login(7002, codes["Лебедева Анастасия"])
    q1 = "Можно ли работать из другого города две недели?"
    out = bot.say(7001, q1)
    assert "ответа нет" in text_of(out)
    out = bot.tap(7001, out, "Передать HR")
    assert "принято" in text_of(out)
    hr = env.login(HR)
    today = hr.get("/").text
    assert "Можно ли работать из другого города" in today and "В очереди" in today  # П1
    queue = hr.get("/queue").text
    assert "Козлов Дмитрий" in queue  # П2
    key = env.deps.repo.tickets(CO)[0].key
    answer = "Можно до 14 дней по согласованию с руководителем."
    r = hr.post(
        f"/queue/{key}/answer",
        data={
            "text": answer,
            "save_kb": "1",
            "basis": "п. 3.2 Правил",
            "question": "Можно ли работать из другого города",
        },
    )
    assert "Ответ отправлен" in r.text and "Сохранён в базу" in r.text
    got = bot.push(7001)
    assert any(answer in o.text and f"№{key}" in o.text for o in got)  # the employee received it
    # another employee asks the same: the base answers, the model was not called
    calls = len(pipe.calls)
    out = bot.say(7002, "Можно ли мне работать из другого города?")
    assert "Ответ HR из базы" in text_of(out) and answer in text_of(out)
    assert len(pipe.calls) == calls
    kb_page = hr.get("/kb").text
    assert "Можно ли работать из другого города" in kb_page and "п. 3.2 Правил" in kb_page
    assert env.deps.repo.kb_entries(CO)[0].uses == 1


# ---------------------------------------------------------------------------------------
# 3. Risk in the rules -> "fix" -> new version -> re-check -> the bot uses the new version
# ---------------------------------------------------------------------------------------


def test_scenario_3_risk_fix_new_version_bot_uses_it() -> None:
    env = make_env()
    doc = upload_rules(env)
    approve(env, doc, 1)
    hr = env.login(HR)
    hr.post(f"/check/{doc}/1/run")
    today = hr.get("/").text
    assert "с риском нарушения" in today  # П1: the risk is visible
    emp, _ = env.deps.staff.invite(CO, "Козлов Дмитрий", "01.01.2024", env.clock.now)
    code = env.deps.bot_auth.issue_code(CO, emp.id, env.clock.now)
    bot = Bot(env, Pipe())
    bot.login(7001, code)
    rules_menu = bot.say(7001, "/rules")
    v1 = bot.tap(7001, rules_menu, "Правила студии")
    assert "один раз в месяц" in text_of(v1) and "Версия 1" in text_of(v1)
    fixed = "Заработная плата выплачивается два раза в месяц: 25 числа и 10 числа."
    r = hr.post(f"/check/{doc}/1/fix", data={"item": "3.1", "text": fixed})
    assert "Создана версия 2" in r.text and "рисков — 0" in r.text  # clean re-check
    # a draft without an order is not applied by the bot
    v_draft = bot.tap(7001, bot.say(7001, "/rules"), "Правила студии")
    assert "один раз в месяц" in text_of(v_draft)
    approve(env, doc, 2, "2", "02.11.2026")
    v2 = bot.tap(7001, bot.say(7001, "/rules"), "Правила студии")
    assert "два раза в месяц" in text_of(v2) and "Версия 2" in text_of(v2)
    assert "с риском нарушения" not in env.login(HR).get("/").text
    rep = env.deps.checker.report(CO, doc, 2)  # type: ignore[union-attr]
    assert rep is not None and not rep.open_risks


# ---------------------------------------------------------------------------------------
# 4. Change of a law article -> answers based on it are marked "check"
# ---------------------------------------------------------------------------------------


def test_scenario_4_law_change_marks_answers() -> None:
    env = make_env()
    d = env.deps
    d.kb.add(CO, question="Сколько дней отпуска", answer="28 дней.", basis="ст. 115 ТК РФ",
             author="Анна", now=env.clock.now)  # fmt: skip
    d.kb.add(CO, question="Как платят за выходной", answer="В двойном размере.", basis="ст. 153 ТК РФ",
             author="Анна", now=env.clock.now)  # fmt: skip
    hr = env.login(HR)
    assert "проверить" not in hr.get("/kb").text.replace("Проверить", "")
    r = hr.post("/kb/law-change", data={"article": "115"})
    assert "Помечено «проверить»: 1" in r.text
    page = hr.get("/kb").text
    assert "1 ответ(ов) нужно пересмотреть" in page and "Сколько дней отпуска" in page
    marked = {e.question for e in d.kbadmin.needs_review(CO)}
    assert marked == {"Сколько дней отпуска"}  # Art. 153 is untouched
    # the affected answer is reviewed and the mark is removed
    r = hr.post(f"/kb/review/{d.kbadmin.needs_review(CO)[0].id}", data={"decision": "keep"})
    assert "Пересмотр завершён" in r.text and not d.kbadmin.needs_review(CO)
    # the same on a rules version change: answers with rules items get a mark
    d.kb.add(CO, question="Сколько отпуск в студии", answer="31 день.", basis="п. 2.1 Правил",
             author="Анна", now=env.clock.now)  # fmt: skip
    doc = upload_rules(env)
    approve(env, doc, 1)
    env.login(HR).post(
        "/docs/upload", data={"doc_id": doc},
        files={"file": ("r.docx", docx_bytes([*RULES, "4. Разное", "4.1. Новый пункт."]), "x")},
    )  # fmt: skip
    approve(env, doc, 2, "2", "03.11.2026")
    assert any(e.basis == "п. 2.1 Правил" for e in d.kbadmin.needs_review(CO))


# ---------------------------------------------------------------------------------------
# 5. Budget exhausted -> action from П9 -> warning in П1
# ---------------------------------------------------------------------------------------


def test_scenario_5_budget_exhausted_action_and_warning() -> None:
    env = make_env()
    d = env.deps
    emp, _ = d.staff.invite(CO, "Козлов Дмитрий", "01.01.2024", env.clock.now)
    code = d.bot_auth.issue_code(CO, emp.id, env.clock.now)
    pipe = Pipe()
    pipe.on(
        "отпуск",
        FlowAnswer("answered", "28 дней.", "law", [Source(1, "ст. 115 ТК РФ", "115", "28", CO)]),
    )
    bot = Bot(env, pipe, gated=True)
    bot.login(7001, code)
    adm = env.login(ADMIN)
    assert "Бюджет месяца исчерпан" not in adm.get("/").text
    assert "28 дней" in text_of(bot.say(7001, "Сколько дней отпуска?"))  # while there is budget
    assert adm.post("/costs/limit", data={"limit": "1000", "action": "queue"})
    d.ops.log_question(CO, masked_question="расход", cost_rub=1000.0, now=env.clock.now)
    calls = len(pipe.calls)
    out = bot.say(7001, "Сколько дней отпуска?")
    assert len(pipe.calls) == calls  # the model was not called
    assert "28 дней" not in text_of(out) and "HR" in text_of(out)  # the question goes to HR
    page = adm.get("/").text
    assert (
        "Бюджет месяца исчерпан" in page
        and "вопросы без готового ответа уходят в очередь HR" in page
    )
    assert 'href="/settings"' in page  # the action is in the settings
    # the administrator chooses another action when the limit is exceeded
    r = adm.post("/costs/limit", data={"limit": "1000", "action": "refuse"})
    assert "Лимит изменён" in r.text
    assert "вежливо отказывает" in adm.get("/").text
    out2 = bot.say(7001, "Сколько дней отпуска теперь?")
    assert text_of(out2) != text_of(out) and len(pipe.calls) == calls
    # HR sees the warning and asks the administrator rather than changing the limit itself
    page_hr = env.login(HR).get("/").text
    assert "Бюджет месяца исчерпан" in page_hr and "Попросить администратора" in page_hr
    # answers from the base keep working when the budget is exhausted
    d.kb.add(CO, question="Как оформить командировку", answer="Заявление за 3 дня.", basis="п. 5.1",
             author="Анна", now=env.clock.now)  # fmt: skip
    assert "Заявление за 3 дня" in text_of(bot.say(7001, "Как оформить командировку?"))
    # new month: spending reset to zero
    env.clock.advance(days=14)
    assert not d.ops.budget(CO, env.clock.now).exhausted


# ---------------------------------------------------------------------------------------
# 6. Dismissal -> deactivation -> the bot does not answer, history is kept per the retention period
# ---------------------------------------------------------------------------------------


def test_scenario_6_dismissal_disable_keeps_history() -> None:
    env = make_env()
    d = env.deps
    emp, _ = d.staff.invite(CO, "Никитин Сергей", "20.01.2023", env.clock.now)
    code = d.bot_auth.issue_code(CO, emp.id, env.clock.now)
    pipe = Pipe()
    pipe.on("отпуск", FlowAnswer("answered", "28 дней.", "law"))
    bot = Bot(env, pipe)
    bot.login(7003, code)
    out = bot.say(7003, "Позвать HR")
    out = bot.tap(7003, out, "Обычный вопрос к HR")
    bot.say(7003, "Оформите мне справку о доходах")
    assert d.repo.tickets(CO)
    hr = env.login(HR)
    assert "Подключён" in hr.get("/staff").text
    r = hr.post(f"/staff/{emp.id}/disable", data={"confirm": "1"})
    assert "Доступ отключён" in r.text
    calls = len(pipe.calls)
    out = bot.say(7003, "Сколько дней отпуска?")
    assert len(pipe.calls) == calls  # the bot does not answer substantively
    assert "доступ" in text_of(out).lower()
    n_tickets = len(d.repo.tickets(CO))
    bot.say(7003, "Хочу создать обращение")
    assert len(d.repo.tickets(CO)) == n_tickets
    # history is kept per the retention period: the request and profile stay in place, the code does not work
    assert any("справку о доходах" in t.text for t in d.repo.tickets(CO))
    assert d.repo.employee(CO, emp.id) is not None
    assert hr.get("/staff").text.count("Доступ отключён") >= 1
    assert d.ops.tech(CO).retention_days == 365  # default retention period is 1 year
    # a new code is not issued to a deactivated employee
    r = hr.post(f"/staff/{emp.id}/newcode", data={})
    assert "отключённым доступом" in r.text
    assert env.clock.now.date() >= date(2026, 11, 18)
