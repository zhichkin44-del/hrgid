"""e2e сценарии SPEC-09 §4.1–4.8 на движке бота (SCREENS Б1–Б11)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from hrgid.bot.auth import AuthService
from hrgid.bot.desk import HrDesk
from hrgid.bot.kb import KbService
from hrgid.bot.models import Employee
from hrgid.bot.repo import Repo
from hrgid.flow.graph import FlowAnswer

from .conftest import COMPANY, NOW, OLGA_TG, make_employee, msk
from .harness import VACATION_ANSWER, Chat, Harness


@pytest.fixture()
def h(repo: Repo, auth: AuthService, desk: HrDesk, kb: KbService) -> Harness:
    return Harness(repo, auth, desk, kb)


def login(h: Harness, tg: int = OLGA_TG, emp: Employee | None = None) -> Chat:
    emp = emp or make_employee(h.repo)
    code = h.auth.issue_code(COMPANY, emp.id, h.now)
    c = h.chat(tg)
    c.say("/start").say(code).tap("Согласен")
    assert "Согласие записано" in c.text
    return c


# --- 4.1 вход ---------------------------------------------------------------------------------


def test_4_1_login_consent_and_menu(h: Harness) -> None:
    emp = make_employee(h.repo)
    code = h.auth.issue_code(COMPANY, emp.id, h.now)
    c = h.chat(OLGA_TG)
    c.say("/start")
    assert "Введите код приглашения" in c.text and "Студия Жичкина" in c.text
    c.say("привет")
    assert "Введите код приглашения" in c.text
    c.say(code)
    assert "Код принят. Здравствуйте, Ольга Смирнова, дизайнер" in c.text
    assert c.buttons == ["Согласен", "Не согласен", "Читать полностью"]
    n = len(c.inbox)
    c.say("Сколько дней отпуска?")
    assert len(c.inbox) == n  # без согласия бот не отвечает
    assert h.repo.employee_by_telegram(OLGA_TG) is None
    c.tap("Читать полностью")
    assert "Оператор" in c.text and "Claude" in c.text
    c.tap("Согласен")
    assert "Согласие записано: 18.11.2026, 15:40" in c.text and c.last.menu
    saved = h.repo.employee_by_telegram(OLGA_TG)
    assert saved is not None and saved.consent_at == NOW
    c.say("/start")
    assert "Чем помочь, Ольга?" in c.text and c.last.menu
    # код одноразовый
    other = h.chat(5002)
    other.say(code)
    assert "Код не найден" in other.text


def test_4_1_refusal_keeps_bot_silent_and_data_unsaved(h: Harness) -> None:
    emp = make_employee(h.repo)
    code = h.auth.issue_code(COMPANY, emp.id, h.now)
    c = h.chat(OLGA_TG)
    c.say(code).tap("Не согласен")
    assert "данные не сохраняются" in c.text and c.buttons == ["Вернуться к согласию"]
    n = len(c.inbox)
    c.say("привет")
    assert len(c.inbox) == n
    stored = h.repo.employee(COMPANY, emp.id)
    assert stored is not None and stored.telegram_id is None and stored.consent_at is None
    c.tap("Вернуться к согласию")
    assert "Здравствуйте, Ольга Смирнова" in c.text
    c.tap("Согласен")
    assert "Согласие записано" in c.text


def test_4_1_five_wrong_codes_block_for_15_minutes(h: Harness) -> None:
    emp = make_employee(h.repo)
    code = h.auth.issue_code(COMPANY, emp.id, h.now)
    c = h.chat(OLGA_TG)
    c.say("ЖС-1111-ААА1")
    assert "1-я ошибка из 5" in c.text and "Осталось 4 попытки" in c.text
    for i in range(2, 5):
        c.say(f"ЖС-1111-ААА{i}")
    assert "4-я ошибка из 5" in c.text and "Осталась 1 попытка" in c.text
    c.say("ЖС-1111-ААА5")
    assert "5-я ошибка подряд" in c.text and "15 минут" in c.text
    assert "Не пришёл код" in c.buttons
    c.say(code)
    assert "закрыт до 15:55" in c.text  # верный код в блоке не принимается
    h.advance(minutes=16)
    c.say(code)
    assert "Код принят" in c.text


# --- 4.2 меню и команды -----------------------------------------------------------------------


def test_4_2_menu_and_commands(h: Harness) -> None:
    c = login(h)
    c.say("/calc")
    assert c.text == "Что посчитать?" and "Отпускные" in c.buttons and "Больничный" in c.buttons
    c.say("/rules")
    assert c.buttons[0] == "Отпуск"
    c.tap("Отпуск")
    assert "Версия 3" in c.text and "4.1. Отпуск — 31" in c.text
    assert "Задать вопрос по этому правилу" in c.buttons
    c.say("/privacy")
    assert "Удалить мои данные" in c.buttons and "1 год" in c.text
    c.say("/help")
    assert "не юридическая консультация" in c.text
    c.say("Мои обращения")
    assert "Обращений пока нет" in c.text
    c.say("Позвать HR")
    assert c.buttons[:2] == ["Обычный вопрос к HR", "Конфиденциально"]
    c.say("Задать вопрос")
    assert "Напишите вопрос" in c.text
    c.say("Правила компании")
    assert "Выберите тему" in c.text


def test_4_2_delete_my_data_request_goes_to_hr(h: Harness) -> None:
    c = login(h)
    c.say("/privacy").tap("Удалить мои данные")
    assert "30 дней" in c.text
    c.tap("Да, удалить")
    assert "Запрос принят" in c.text
    c.pushes()
    assert any("Запрос на удаление данных" in o.text for o in h.hr_inbox())
    assert [t.kind for t in h.repo.tickets(COMPANY)] == ["delete_request"]


# --- 4.3 ответ с источником -------------------------------------------------------------------------


def test_4_3_answer_with_source_quotes_and_rating(h: Harness) -> None:
    h.pipeline.on("отпуска", VACATION_ANSWER)
    c = login(h)
    c.say("Сколько дней отпуска мне положено в год?")
    assert c.text.startswith("28 дней по закону, в Студии — 31.")
    assert "Источник: ст. 115 ТК РФ; Правила Студии, п. 4.1" in c.text
    assert "не юридическая консультация" in c.text  # в первом ответе дня
    assert c.buttons == ["Показать цитаты", "👍", "👎", "Спросить HR"]
    c.tap("Показать цитаты")
    assert c.last.edit and "«Ежегодный основной оплачиваемый отпуск" in c.text
    c.tap("Свернуть")
    assert c.last.edit and "«Ежегодный" not in c.text
    c.tap("👍")
    assert "оценка записана" in c.text
    assert [f.vote for f in h.repo.feedback(COMPANY)] == ["up"]
    c.say("А сколько дней отпуска на испытательном?")
    assert "не юридическая консультация" not in c.text  # один раз в день


def test_4_3_answer_is_cut_to_900_chars(h: Harness) -> None:
    h.pipeline.on("длинный", FlowAnswer("answered", "слово " * 400, "law"))
    c = login(h)
    c.say("длинный вопрос")
    assert len(c.text.split("\n\n")[0]) <= 900


def test_4_3_no_answer_offers_hr_and_passes_question(h: Harness) -> None:
    c = login(h)
    c.say("Компенсирует ли Студия абонемент в спортзал?")
    assert "В законе и правилах компании ответа нет" in c.text
    assert c.buttons == ["Передать HR", "Спросить иначе"]
    c.tap("Спросить иначе")
    assert "Напишите вопрос" in c.text
    c.say("Компенсирует ли Студия абонемент в спортзал?").tap("Передать HR")
    assert "Обращение №101 принято" in c.text
    c.pushes()
    assert any("абонемент" in o.text for o in h.hr_inbox())


def test_4_3_off_topic_and_company_rule_beats_law(h: Harness) -> None:
    h.pipeline.on("погода", FlowAnswer("off_topic", ""))
    c = login(h)
    c.say("Какая завтра погода?")
    assert "только на вопросы о работе" in c.text
    assert c.buttons[:2] == ["Задать кадровый вопрос", "Позвать HR"]


def test_4_3_answer_from_hr_knowledge_base(h: Harness) -> None:
    from hrgid.bot.desk import KbParams

    other = make_employee(h.repo, "e2", "Иван Петров", telegram_id=5002, consent_at=NOW)
    t = h.desk.create(other, "Можно ли перенести отпуск с декабря на январь?", NOW)
    answer = "Можно по заявлению и с согласия работодателя."
    h.desk.answer(
        COMPANY, t.key, "Анна", answer, NOW, save_to_kb=KbParams(t.text, answer, "ст. 124 ТК РФ")
    )
    c = login(h)
    c.say("Можно перенести отпуск с декабря на январь?")
    assert "Ответ HR из базы" in c.text and "ст. 124 ТК РФ" in c.text and "Анна" in c.text
    assert h.pipeline.requests == []  # модель не вызывалась


def test_4_3_service_failure_saves_question_as_ticket(h: Harness) -> None:
    h.pipeline.on("отпуск", RuntimeError("модель недоступна"))
    c = login(h)
    c.say("Сколько дней отпуска?")
    assert "сервис временно недоступен" in c.text and "обращение №101" in c.text
    assert "Правила компании" in c.buttons and "Посчитать" in c.buttons
    assert h.repo.tickets(COMPANY)[0].kind == "failure"
    h.pipeline.script.clear()
    h.pipeline.on("налог", FlowAnswer("error", "Сервис недоступен"))
    c.say("Что с налогом?")
    assert "обращение №102" in c.text


# --- 4.4 диалог и профиль -----------------------------------------------------------------------------


def test_4_4_dialog_uses_last_six_messages(h: Harness) -> None:
    h.pipeline.on("", VACATION_ANSWER)
    c = login(h)
    qs = [f"вопрос {i}" for i in range(8)]
    for q in qs:
        c.say(q)
    assert list(h.pipeline.requests[-1].history) == qs[-7:-1]
    assert h.pipeline.requests[-1].question == qs[-1]
    assert h.pipeline.requests[-1].profile is None  # профиль в общий ответ не уходит (кэш, §9)


def test_4_4_profile_is_shown_with_import_date_and_missing_data_is_asked(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Отпускные")
    assert "данные на 12.11.2026" in c.text and "оклад 80 000 ₽" in c.text
    c.say("С 14 декабря, 14 дней")
    assert "нет заработка" in c.text and "80 000 ₽ × 12 = 960 000 ₽" in c.text  # спрашивает
    assert c.buttons[:3] == ["Да, верно", "Ввести свою сумму", "Были премии"]


# --- 4.5 расчёты -------------------------------------------------------------------------------------------


def test_4_5_vacation_pay_confirmed_and_own_sum(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Отпускные").say("С 14 декабря, 14 дней").tap("Да, верно")
    assert "38 225,32 ₽ до НДФЛ" in c.text
    assert "4 969 ₽" in c.text and "33 256,32 ₽" in c.text and "ст. 139" in c.text
    assert "до 11.12.2026" in c.text and "ст. 136" in c.text
    assert c.buttons == ["Пересчитать", "Спросить HR", "👍", "👎", "Другой расчёт"]
    c.tap("Пересчитать").say("С 14 декабря, 14 дней").tap("Ввести свою сумму")
    assert "до вычета НДФЛ" in c.text
    c.say("много")
    assert "Не получилось прочитать сумму" in c.text
    c.say("1020000")
    assert "40 614,28 ₽ до НДФЛ" in c.text and "только для этого расчёта" in c.text
    assert h.repo.employee_by_telegram(OLGA_TG).earnings_12m is None  # type: ignore[union-attr]
    c.tap("Спросить HR")
    assert "принято" in c.text


def test_4_5_overtime_with_annual_limit_per_art_152(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Сверхурочные")
    assert "оклад 80 000 ₽" in c.text
    c.say("12 ноября 3 часа, 17 ноября 2 часа")
    assert "4 025,12 ₽ до НДФЛ" in c.text and "503.14" in c.text
    assert "120" in c.text and "ст. 152" in c.text and "сверх лимита — двойной" in c.text
    h.repo.save_employee(
        h.repo.employee_by_telegram(OLGA_TG).model_copy(update={"overtime_ytd": Decimal(119)})  # type: ignore[union-attr]
    )
    c.tap("Пересчитать").say("12 ноября 3 часа")
    assert "С множителем 1,5 — 1 ч, с множителем 2 — 2 ч" in c.text
    c.say("ерунда")  # после итога свободный текст — уже обычный вопрос, не расчёт
    assert "ответа нет" in c.text


def test_4_5_overtime_bad_input_repeats_question(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Сверхурочные").say("12 ноября 30 часов")
    assert "не больше 24" in c.text and "по дням" in c.text
    c.say("столько-то")
    assert "Не получилось прочитать часы" in c.text


def test_4_5_weekend_work(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Работа в выходной").say("18 ноября")
    assert "Это не выходной" in c.text
    c.say("21 ноября").say("8")
    assert c.buttons[:2] == ["Двойная оплата", "Одинарная + день отдыха"]
    c.tap("Двойная оплата")
    assert "8 050,24 ₽ до НДФЛ" in c.text


def test_4_5_sick_leave(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Больничный")
    assert "диагноз" in c.text.lower() and "не спрашиваю и не храню" in c.text
    c.say("6 лет 4 месяца").say("с 10 по 16 ноября")
    assert "2024" in c.text and "2025" in c.text
    c.say("2024 — 700 000, 2025 — 960 000")
    assert "12 734,26 ₽ до НДФЛ" in c.text and "730" in c.text
    c.say("/calc").tap("Больничный").say("-3")
    assert "стаж" in c.text.lower()


def test_4_5_dates_and_terms(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Последний рабочий день").say("18 ноября")
    assert "02.12.2026" in c.text
    c.tap("Другой расчёт").tap("Срок выплаты")
    assert c.buttons[:3] == ["Отпускные", "Расчёт при увольнении", "Зарплата"]
    c.tap("Отпускные").say("14 декабря")
    assert "11.12.2026" in c.text and "ст. 136" in c.text
    c.tap("Другой расчёт").tap("Испытательный срок").say("2 ноября").say("4")
    assert "не более 3 месяцев" in c.text and "руководителям" in c.text
    c.say("3").say("0")
    assert "01.02.2027" in c.text and "ст. 70" in c.text


def test_4_5_accrued_vacation_and_compensation(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Накопленный отпуск").say("01.01.2020")
    assert "не раньше даты приёма" in c.text
    c.tap("Сегодня")
    assert "Накопленный отпуск на 18.11.2026" in c.text
    c.tap("Другой расчёт").tap("Компенсация при увольнении").say("30 ноября").say("12")
    c.tap("Да, верно")
    assert "Компенсация:" in c.text and "до НДФЛ" in c.text


def test_4_5_dialog_timeout_15_minutes(h: Harness) -> None:
    c = login(h)
    c.say("/calc").tap("Отпускные")
    h.advance(minutes=14)
    assert h.engine.tick(h.now) == []
    h.advance(minutes=2)
    outs = h.engine.tick(h.now)
    assert len(outs) == 1 and "расчёт отменён, ничего не сохранено" in outs[0].text
    assert [b.text for r in outs[0].rows for b in r] == ["Посчитать", "В меню"]
    # то же без фонового тика: ленивая проверка на следующем сообщении
    c.say("/calc").tap("Отпускные")
    h.advance(minutes=20)
    c.say("С 14 декабря, 14 дней")
    assert "расчёт отменён" in c.text


# --- 4.6 обращения к HR -------------------------------------------------------------------------------


def test_4_6_ticket_hr_answer_in_chat_and_rating(h: Harness) -> None:
    c = login(h)
    c.say("Позвать HR").tap("Обычный вопрос к HR")
    c.say("Можно ли взять отпуск за свой счёт на две недели?")
    assert "Обращение №101 принято" in c.text and "4 рабочих часа" in c.text
    assert "завтра до 10:40" in c.text
    c.pushes()
    [note] = h.hr_inbox()
    assert "Ольга Смирнова" in note.text and "отпуск за свой счёт" in note.text
    c.say("Мои обращения")
    assert "№101 · 18.11" in c.text and "в очереди" in c.text
    c.tap("Открыть №101")
    assert c.buttons == ["Дописать", "Отозвать обращение", "Мои обращения"]
    c.tap("Дописать").say("Нужно с 1 по 14 декабря")
    assert "Дописал" in c.text
    h.desk.answer(COMPANY, "101", "Анна Воронова", "Да, по заявлению.", h.now)
    got = c.pushes()
    assert len(got) == 1 and "Ответ HR · обращение №101" in got[0].text
    assert "Да, по заявлению." in got[0].text
    c.tap("👍")
    assert "Обращение №101 закрыто" in c.text
    c.say("Мои обращения")
    assert "закрыто" in c.text


def test_4_6_withdraw_ticket(h: Harness) -> None:
    c = login(h)
    c.say("Позвать HR").tap("Обычный вопрос к HR").say("вопрос")
    c.say("Мои обращения").tap("Открыть №101").tap("Отозвать обращение")
    c.tap("Да, отозвать")
    assert "отозвано" in c.text
    assert h.desk.list_tickets(COMPANY, "all", h.now) == []


def test_4_6_outside_hr_hours_autoreply_with_urgent_contact(h: Harness) -> None:
    h.now = msk(2026, 11, 17, 21, 36)
    c = login(h)
    c.say("Позвать HR")
    assert "Сейчас вне часов HR" in c.text and "пн–пт с 10:00 до 19:00 (мск)" in c.text
    assert "завтра с 10:00" in c.text and "+7 900 000-00-00" in c.text
    c.tap("Оставить обращение").say("Нужна справка о доходах")
    assert "Обращение №101 принято" in c.text and "вне часов" in c.text
    assert "завтра до 14:00" in c.text and "+7 900 000-00-00" in c.text
    # то же, если вопрос без ответа в источниках
    c.say("абонемент?")
    assert "вне часов HR" in c.text and "+7 900" in c.text


def test_4_6_foreign_ticket_not_accessible(h: Harness) -> None:
    other = make_employee(h.repo, "e2", "Иван Петров", telegram_id=5002, consent_at=NOW)
    t = h.desk.create(other, "личное", NOW)
    c = login(h)
    c.data(f"t:open:{t.key}")
    assert "личное" not in c.text and "Чем помочь" in c.text
    c.data(f"t:wdy:{t.key}")
    assert h.desk.get(COMPANY, t.key).status == "queue"


def test_4_6_announcement_read_and_dates(h: Harness) -> None:
    c = login(h)
    ann = h.desk.publish_announcement(
        COMPANY, "График отпусков на 2027 год", "Пишите желаемые даты до 1 декабря", "Анна", h.now,
        asks_dates=True,
    )  # fmt: skip
    [push] = c.pushes()
    assert "📢 Объявление от HR" in push.text and "до 1 декабря" in push.text
    assert c.buttons == ["Прочитано", "Написать даты HR"]
    c.tap("Написать даты HR").say("с 1 по 14 июля")
    assert "Передал HR ваши даты" in c.text
    assert h.desk.unread(COMPANY, ann.id) == []
    kinds = [t.kind for t in h.repo.tickets(COMPANY)]
    assert kinds == ["dates"]


# --- 4.7 конфиденциальное обращение ---------------------------------------------------------------------


def test_4_7_confidential_flow(h: Harness) -> None:
    c = login(h)
    c.say("Позвать HR").tap("Конфиденциально")
    assert "без имени" in c.text and "не попадают в статистику и в базу ответов" in c.text
    c.say("Руководитель пишет мне по ночам и требует ответа в выходные")
    assert "Обращение К-1 передано HR" in c.text and "без вашего имени" in c.text
    assert c.buttons == ["Раскрыть имя", "Дописать", "Удалить обращение"]
    c.pushes()
    [note] = h.hr_inbox()
    assert "Смирнова" not in note.text and "К-1" in note.text and "по ночам" in note.text
    assert all(r["text"] == "" for r in h.desk.analytics_rows(COMPANY))
    # HR отвечает через бота; имя скрыто
    view = h.desk.view(h.desk.get(COMPANY, "K1"), h.now)
    assert view.employee_name is None
    h.desk.answer(COMPANY, "K1", "Анна", "Спасибо, что написали. Разберёмся.", h.now)
    [reply] = c.pushes()
    assert "обращение К-1" in reply.text
    # раскрытие имени — только по решению сотрудника
    c.say("Мои обращения").tap("Открыть К-1")
    h.chat(OLGA_TG).data("conf:reveal:K1")
    assert "Отменить это потом нельзя" in h.chat(OLGA_TG).data("conf:reveal:K1").text
    c.data("conf:ry:K1")
    assert "HR теперь видит ваше имя" in c.text
    assert h.desk.view(h.desk.get(COMPANY, "K1"), h.now).employee_name == "Ольга Смирнова"
    c.pushes()
    assert any("раскрыл имя: Ольга Смирнова" in o.text for o in h.hr_inbox())


def test_4_7_confidential_delete_with_10_second_undo(h: Harness) -> None:
    c = login(h)
    c.say("Позвать HR").tap("Конфиденциально").say("жалоба на конфликт")
    c.tap("Удалить обращение")
    assert "10 секунд" in c.text
    c.tap("Да, удалить")
    assert "У вас есть 10 секунд" in c.text
    assert h.desk.list_tickets(COMPANY, "conf", h.now) == []
    c.tap("Вернуть обращение")
    assert "возвращено" in c.text and len(h.desk.list_tickets(COMPANY, "conf", h.now)) == 1
    c.say("Мои обращения").tap("Открыть К-1").tap("Удалить обращение").tap("Да, удалить")
    h.advance(seconds=11)
    c.tap("Вернуть обращение")
    assert "удалено окончательно" in c.text
    assert h.desk.get(COMPANY, "K1").text == ""


def test_4_7_confidential_append(h: Harness) -> None:
    c = login(h)
    c.say("/hr").tap("Конфиденциально").say("первое")
    c.tap("Дописать").say("второе")
    assert "Дописал" in c.text
    c.pushes()
    assert "К-1" in h.hr_inbox()[-1].text and "Смирнова" not in h.hr_inbox()[-1].text


# --- 4.8 служебные сценарии -------------------------------------------------------------------------------


def test_4_8_code_did_not_arrive(h: Harness) -> None:
    emp = make_employee(h.repo)
    c = h.chat(OLGA_TG)
    c.say("ЖС-1111-ААА1").tap("Не пришёл код")
    assert "Я передал HR просьбу выдать новый код" in c.text
    assert c.buttons == ["Ввести код", "В начало"]
    c.pushes()
    assert any("Не пришёл код приглашения" in o.text for o in h.hr_inbox())
    assert [t.kind for t in h.repo.tickets(COMPANY)] == ["new_code"]
    new = h.auth.issue_code(COMPANY, emp.id, h.now)  # HR перевыпускает (П6)
    c.tap("Ввести код").say(new)
    assert "Код принят" in c.text


def test_4_8_new_telegram_unlinks_old_chat(h: Harness) -> None:
    emp = make_employee(h.repo)
    old = login(h, 5001, emp)
    new_code = h.auth.issue_code(COMPANY, emp.id, h.now)
    new = h.chat(5002)
    new.say(new_code)
    assert "нового аккаунта Telegram" in new.text and "Прежний чат отвязан" in new.text
    assert new.buttons == ["Это я, всё верно", "Это не я"]
    assert h.repo.employee_by_telegram(5002) is not None
    assert h.repo.employee_by_telegram(5001) is None
    old.say("вопрос")
    assert "Введите код приглашения" in old.text  # в старом чате бот больше не отвечает
    new.tap("Это не я")
    assert "Спасибо, что сообщили" in new.text
    assert h.repo.employee_by_telegram(5002) is None  # привязка снята до входа по новому коду
    assert h.auth.check_code(5003, new_code, h.now).status == "bad"  # код отозван
    assert [t.kind for t in h.repo.tickets(COMPANY)] == ["wrong_login"]
    h.engine.flush_outbox(lambda o: True)


def test_4_8_new_telegram_confirmed(h: Harness) -> None:
    emp = make_employee(h.repo)
    login(h, 5001, emp)
    new = h.chat(5002)
    new.say(h.auth.issue_code(COMPANY, emp.id, h.now)).tap("Это я, всё верно")
    assert "Чем помочь" in new.text


def test_4_8_disabled_employee_gets_access_closed(h: Harness) -> None:
    c = login(h)
    emp = h.repo.employee_by_telegram(OLGA_TG)
    assert emp is not None
    h.repo.save_employee(emp.model_copy(update={"status": "disabled"}))
    c.say("Сколько дней отпуска?")
    assert "Доступ закрыт" in c.text and "Студия Жичкина" in c.text
    assert c.last.rows == []
    c.data("menu")
    assert "Доступ закрыт" in c.text
    # отключённому не выдаётся и новый вход
    code = h.auth.issue_code(COMPANY, emp.id, h.now)
    g = h.chat(7777)
    g.say(code)
    assert "Доступ закрыт" in g.text


def test_4_9_limits_not_legal_advice_in_help_and_off_topic(h: Harness) -> None:
    c = login(h)
    c.say("/help")
    assert "не юридическая консультация" in c.text
