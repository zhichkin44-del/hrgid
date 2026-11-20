"""П7 аналитика, П8 расходы, П9 настройки, П10 журнал: данные, валидация, права."""

from __future__ import annotations

import io
import zipfile

from .conftest import ADMIN, CO, HR, make_env, msk
from .seed import seed

# ----- П7 -----------------------------------------------------------------------------


def test_analytics_empty_state_and_error_state() -> None:
    env = make_env()
    hr = env.login(HR)
    assert "Данных пока нет" in hr.get("/analytics").text


def test_analytics_topics_growth_share_and_confidential_excluded() -> None:
    env = make_env()
    s = seed(env)
    d = env.deps
    d.ops.log_question(CO, masked_question="Сколько дней отпуска у [ФИО_1]")
    d.ops.log_question(CO, masked_question="Когда зарплата", status="no_answer")
    d.desk.answer(CO, s.t1.key, "Анна", "Можно.", env.clock.now)
    d.desk.rate(d.repo.ticket(CO, s.t1.key), "up")  # type: ignore[arg-type]
    r = env.login(HR).get("/analytics")
    assert "Отпуск" in r.text and "Зарплата и выплаты" in r.text
    assert "притесняет" not in r.text  # конфиденциальное не попадает
    assert "Частые вопросы без ответа" in r.text and "Добавить в базу" in r.text
    topics = {t.topic: t for t in d.analytics.topics(CO, env.clock.now)}
    assert topics["Отпуск"].total >= 3 and topics["Отпуск"].up == 1
    assert topics["Зарплата и выплаты"].without_hr == 1  # no_answer не считается «без HR»
    assert topics["Отпуск"].without_hr >= 2
    # конфиденциальный текст не попал в строки аналитики
    assert all("притесн" not in r["text"] for r in d.desk.analytics_rows(CO))
    detail = env.login(HR).get("/analytics/topic?name=Отпуск")
    assert "Тема «Отпуск»" in detail.text


def test_analytics_unanswered_dedup_and_link_by_ticket_key() -> None:
    env = make_env()
    s = seed(env)
    env.ticket(s.igor, "Можно ли перенести отпуск на январь?")
    items = env.deps.analytics.frequent_unanswered(CO)
    assert items and items[0][2] == 2  # два похожих обращения
    r = env.login(HR).get("/analytics")
    assert f"/kb/new?from={items[0][0]}" in r.text  # в адресе номер, а не текст вопроса


# ----- П8 -----------------------------------------------------------------------------


def test_costs_hr_sees_only_spend_and_limit() -> None:
    env = make_env()
    seed(env)
    r = env.login(HR).get("/costs")
    assert "Расход за месяц" in r.text
    assert "Технические показатели" in r.text and "Медленные 5" not in r.text
    assert "Разбор запросов" not in r.text and "Сбои провайдера" not in r.text
    assert "Доступно администратору" in r.text


def test_costs_admin_sees_perf_traces_without_pd() -> None:
    env = make_env()
    seed(env)
    r = env.login(ADMIN).get("/costs")
    assert "Медленные 5" in r.text and "Разбор запросов" in r.text
    assert "[ФИО_1]" in r.text  # метки вместо имён
    assert "Качество ответов по версиям" in r.text


def test_limit_change_validation_and_effects() -> None:
    env = make_env()
    seed(env)
    adm = env.login(ADMIN)
    assert (
        "Сумма — число" in adm.post("/costs/limit", data={"limit": "много", "action": "queue"}).text
    )
    assert (
        "не меньше 1 000" in adm.post("/costs/limit", data={"limit": "500", "action": "queue"}).text
    )
    r = adm.post("/costs/limit", data={"limit": "15 000", "action": "refuse"})
    assert "Лимит изменён" in r.text
    t = env.deps.ops.tech(CO)
    assert t.budget_rub == 15000 and t.over_action == "refuse"
    hr = env.login(HR)
    assert hr.get("/costs/limit").status_code == 403
    assert hr.post("/costs/limit", data={"limit": "99999", "action": "queue"}).status_code == 403
    assert env.deps.ops.tech(CO).budget_rub == 15000


def test_hr_asks_admin_to_change_limit() -> None:
    env = make_env()
    hr = env.login(HR)
    assert "Напишите, что нужно" in hr.post("/costs/ask", data={"text": ""}).text
    assert "Запрос отправлен" in hr.post("/costs/ask", data={"text": "Нужно больше"}).text
    adm = env.login(ADMIN)
    assert "Нужно больше" in adm.get("/settings").text


# ----- П9 -----------------------------------------------------------------------------


def test_settings_hr_has_no_access_with_ask_link() -> None:
    env = make_env()
    r = env.login(HR).get("/settings")
    assert r.status_code == 403 and "только для администратора" in r.text
    assert "Попросить администратора" in r.text
    for url in ("/settings/model", "/settings/export", "/settings/delete"):
        assert env.login(HR).get(url).status_code == 403
    assert env.login(HR).post("/settings", data={"sla_hours": "1"}).status_code == 403


def test_settings_save_and_validation() -> None:
    env = make_env()
    adm = env.login(ADMIN)
    ok = {
        "hours_start": "09:00", "hours_end": "18:00", "sla_hours": "6",
        "urgent_contact": "Анна: +7 900 111-22-33", "greeting": "Привет", "escalation": "Админу",
        "retention_days": "365", "utc_offset": "3",
    }  # fmt: skip
    assert (
        "Конец рабочего дня раньше начала"
        in adm.post("/settings", data={**ok, "hours_end": "08:00"}).text
    )
    assert (
        "срочный контакт" in adm.post("/settings", data={**ok, "urgent_contact": " "}).text.lower()
    )
    assert "от 1 до 72" in adm.post("/settings", data={**ok, "sla_hours": "0"}).text
    assert "от 30 дней" in adm.post("/settings", data={**ok, "retention_days": "3"}).text
    r = adm.post("/settings", data=ok)
    assert "Настройки сохранены" in r.text
    s = env.deps.repo.settings(CO)
    assert s.sla_hours == 6 and s.hours_start.hour == 9 and "111-22-33" in s.urgent_contact


def test_model_switch_and_claude_warning_needs_ack() -> None:
    env = make_env()
    adm = env.login(ADMIN)
    assert env.deps.ops.tech(CO).provider == "gigachat"  # российская по умолчанию
    r = adm.post("/settings/model", data={"provider": "claude"})
    assert "Вопросы уйдут за границу" in r.text and env.deps.ops.tech(CO).provider == "gigachat"
    r = adm.post("/settings/model", data={"provider": "claude", "confirm_claude": "1"})
    assert "Сотрудники уведомлены" in r.text and env.deps.ops.tech(CO).provider == "gigachat"
    r = adm.post("/settings/model", data={"provider": "claude", "confirm_claude": "1", "ack": "1"})
    assert "Модель изменена" in r.text and env.deps.ops.tech(CO).provider == "claude"
    adm.post("/settings/model", data={"provider": "yandex"})
    assert env.deps.ops.tech(CO).provider == "yandex" and not env.deps.ops.tech(CO).claude_ack


def test_export_zip_has_no_confidential_and_is_admin_only_download() -> None:
    env = make_env()
    s = seed(env)
    adm = env.login(ADMIN)
    r = adm.post("/settings/export", data={"since": ""})
    assert "Файл готов" in r.text
    token = env.store.all("pexport", CO)[0]["token"]
    z = adm.get(f"/settings/export/{token}")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert {"employees.json", "tickets.json", "kb.json", "journal.json"} <= set(zf.namelist())
    tickets = zf.read("tickets.json").decode()
    assert "перенести отпуск" in tickets and "притесняет" not in tickets
    assert env.mailer.sent[-1][0] == ADMIN  # ссылка ушла на почту администратора
    assert env.login(HR).get(f"/settings/export/{token}").status_code == 403
    env.clock.advance(hours=25)
    assert env.login(ADMIN).get(f"/settings/export/{token}").status_code == 404
    assert s.conf.confidential


def test_company_delete_two_steps_with_name() -> None:
    env = make_env()
    seed(env)
    other = env.login("boss@northwind.test")
    other_before = len(env.deps.repo.employees("northwind"))
    adm = env.login(ADMIN)
    r = adm.post("/settings/delete", data={})
    assert "Сначала выгрузите" in r.text
    r = adm.post("/settings/delete", data={"exported": "1"})
    assert "Подтвердите удаление" in r.text and "Шаг 2 из 2" in r.text
    r = adm.post("/settings/delete/confirm", data={"name": "не то"})
    assert "Название не совпало" in r.text and env.deps.repo.employees(CO)
    r = adm.post("/settings/delete/confirm", data={"name": "Студия Жичкина"})
    assert str(r.url).endswith("/login")
    assert env.deps.repo.employees(CO) == [] and env.deps.repo.tickets(CO) == []
    assert env.deps.docs.documents(CO) == [] and env.deps.repo.kb_entries(CO) == []
    assert env.deps.auth.users(CO) == [] and env.store.all("paudit", CO) == []
    assert adm.get("/").url.path == "/login"  # сессии закрыты
    assert len(env.deps.repo.employees("northwind")) == other_before  # чужая компания цела
    assert other.get("/").status_code == 200


# ----- П10 ----------------------------------------------------------------------------


def test_journal_records_actions_without_message_text() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(f"/queue/{s.t1.key}/answer", data={"text": "Секретный текст ответа сотруднику"})
    r = hr.get("/journal")
    assert "Ответил на обращение" in r.text and "Секретный текст" not in r.text
    assert "Журнал пуст" not in r.text


def test_journal_empty_filter_and_entry_window() -> None:
    env = make_env()
    hr = env.login(HR)
    assert "Журнал пуст" in hr.get("/journal").text
    s = seed(env)
    hr.post(f"/queue/{s.t1.key}/answer", data={"text": "ок"})
    assert "По фильтру ничего нет" in hr.get("/journal?kind=kb.add").text
    r = hr.get("/journal?kind=ticket.answer&since=01.11.2026")
    assert "Ответил на обращение" in r.text
    n = env.deps.journal.entries(CO)[0].n
    assert "Запись журнала" in hr.get(f"/journal/{n}").text
    assert hr.get("/journal/zzz").status_code == 404


def test_journal_login_log_admin_only() -> None:
    env = make_env()
    hr = env.login(HR)
    assert "доступно администратору" in hr.get("/journal?tab=logins").text.lower()
    assert "Вошёл" not in hr.get("/journal?tab=logins").text
    adm = env.login(ADMIN)
    r = adm.get("/journal?tab=logins")
    assert "Вошёл" in r.text and "Код отправлен" in r.text


def test_journal_export_csv_pdf_and_period_limit() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(f"/queue/{s.t1.key}/answer", data={"text": "ок"})
    r = hr.post(
        "/journal/export", data={"since": "01.11.2026", "until": "30.11.2026", "fmt": "csv"}
    )
    assert r.headers["content-type"].startswith("text/csv") and "Ответил на обращение" in r.text
    r = hr.post(
        "/journal/export", data={"since": "01.11.2026", "until": "30.11.2026", "fmt": "pdf"}
    )
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    r = hr.post(
        "/journal/export", data={"since": "01.01.2025", "until": "30.11.2026", "fmt": "csv"}
    )
    assert "не больше 365 дней" in r.text


def test_budget_exhausted_flow_p1_warning_and_gate() -> None:
    env = make_env()
    seed(env)
    d = env.deps
    d.ops.log_question(CO, masked_question="большой расход", cost_rub=10000, now=env.clock.now)
    r = env.login(HR).get("/")
    assert "Бюджет месяца исчерпан" in r.text
    assert d.ops.budget(CO, env.clock.now).exhausted
    assert msk(2026, 11, 18).month == 11
