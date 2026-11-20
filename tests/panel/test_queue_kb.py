"""П1–П3: «Сегодня», очередь, база ответов — действия и состояния."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hrgid.bot.models import Ticket

from .conftest import ADMIN, CO, HR, HR2, Env, make_env
from .seed import seed


def test_today_empty_new_company_shows_start() -> None:
    env = make_env()
    r = env.login(HR).get("/")
    assert "Начало работы" in r.text and "Загрузите правила компании" in r.text


def test_today_empty_all_clear_state() -> None:
    env = make_env()
    env.employee(name="Тест Тестов")
    r = env.login(HR).get("/")
    assert "Очередь пуста, рисков нет" in r.text


def test_today_tiles_overdue_and_mine() -> None:
    env = make_env()
    s = seed(env)
    env.deps.desk.assign(CO, s.t1.key, HR)
    env.clock.advance(hours=6)  # SLA 4 рабочих часа истёк
    r = env.login(HR).get("/")
    assert "Просрочено" in r.text and "Назначено мне" in r.text
    assert f"№{s.t1.key}" in r.text


def test_today_shows_open_risks_for_draft_version() -> None:
    env = make_env()
    s = seed(env)
    adm = env.login(ADMIN)
    adm.post(f"/check/{s.doc_id}/1/run")
    r = adm.get("/")
    assert "с риском нарушения" in r.text and "проект, ещё не утверждён" in r.text


def test_rating_down_review_flow() -> None:
    env = make_env()
    s = seed(env)
    d = env.deps
    d.desk.answer(CO, s.t1.key, "Анна", "Можно по заявлению.", env.clock.now)
    d.desk.rate(d.repo.ticket(CO, s.t1.key), "down")  # type: ignore[arg-type]
    hr = env.login(HR)
    assert "Оценок 👎 за сутки" in hr.get("/").text
    r = hr.get(f"/rating/{s.t1.key}")
    assert "Разбор оценки" in r.text and "Можно по заявлению." in r.text
    r = hr.post(f"/rating/{s.t1.key}", data={"decision": "ok"})
    assert "Оценка разобрана" in r.text
    assert d.ratings.open_items(CO, env.clock.now) == []


def test_rating_of_confidential_is_not_reachable() -> None:
    env = make_env()
    s = seed(env)
    assert env.login(HR).get(f"/rating/{s.conf.key}").status_code == 404


# ----- П2 -----------------------------------------------------------------------------


def test_queue_list_card_and_confidential_hidden() -> None:
    env = make_env()
    seed(env)
    hr = env.login(HR)
    r = hr.get("/queue")
    assert "Можно ли перенести отпуск" in r.text
    assert "Ольга Смирнова" in r.text
    r = hr.get("/queue?tab=conf")
    assert "автор скрыт" in r.text and "Ольга Смирнова" not in r.text.split("<main")[1]


def test_answer_goes_to_employee_and_leaves_queue() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post(f"/queue/{s.t1.key}/answer", data={"text": "Можно: заявление за 14 дней."})
    assert "Ответ отправлен" in r.text
    t = env.deps.repo.ticket(CO, s.t1.key)
    assert t is not None and t.status == "answered"
    pending = env.deps.repo.outbox_pending()
    assert any("Можно: заявление" in o.text and o.chat_id == 5001 for _, _, o in pending)
    assert s.t1.key not in {v.key for v in env.deps.desk.list_tickets(CO, "all", env.clock.now)}


def test_empty_answer_is_rejected_with_message() -> None:
    env = make_env()
    s = seed(env)
    r = env.login(HR).post(f"/queue/{s.t1.key}/answer", data={"text": "  "})
    assert "Напишите ответ" in r.text
    assert env.deps.repo.ticket(CO, s.t1.key).status == "queue"  # type: ignore[union-attr]


def test_send_window_and_save_to_kb_needs_basis() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post(f"/queue/{s.t1.key}/w/send", data={"text": "Можно по заявлению."})
    assert "Отправить ответ?" in r.text and "Можно по заявлению." in r.text
    r = hr.post(f"/queue/{s.t1.key}/answer", data={"text": "Можно.", "save_kb": "1", "basis": ""})
    assert "Укажите основание" in r.text
    assert env.deps.repo.ticket(CO, s.t1.key).status == "queue"  # type: ignore[union-attr]
    r = hr.post(
        f"/queue/{s.t1.key}/answer",
        data={
            "text": "Можно.",
            "save_kb": "1",
            "basis": "ст. 124 ТК РФ",
            "question": "Перенос отпуска",
        },
    )
    assert "Сохранён в базу" in r.text
    assert any(e.basis == "ст. 124 ТК РФ" for e in env.deps.repo.kb_entries(CO))


def test_confidential_answer_never_goes_to_kb() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    before = len(env.deps.repo.kb_entries(CO))
    hr.post(
        f"/queue/{s.conf.key}/answer",
        data={"text": "Приходите, поговорим.", "save_kb": "1", "basis": "ст. 2"},
    )
    assert len(env.deps.repo.kb_entries(CO)) == before
    assert hr.post(f"/queue/{s.conf.key}/savekb", data={"question": "q"}).status_code == 404


def test_assign_to_colleague_and_away_blocked() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post(f"/queue/{s.t1.key}/assign", data={"to": HR2, "comment": "ты в курсе"})
    assert "в отпуске" in r.text  # Олег в отпуске
    r = hr.post(f"/queue/{s.t1.key}/assign", data={"to": ""})
    assert "Выберите коллегу" in r.text
    r = hr.post(f"/queue/{s.t1.key}/assign", data={"to": ADMIN, "comment": "x" * 501})
    assert "не длиннее 500" in r.text
    hr.post(f"/queue/{s.t1.key}/assign", data={"to": ADMIN})
    adm = env.login(ADMIN)
    assert "Можно ли перенести отпуск" in adm.get("/queue?tab=mine").text


def test_template_from_kb_fills_draft_without_sending() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post(f"/queue/{s.t1.key}/w/tpl", data={"text": "", "q": "отпуска"})
    assert "Шаблон из базы" in r.text and "28 календарных дней" in r.text
    r = hr.post(f"/queue/{s.t1.key}/tpl", data={"entry": str(s.kb_id)})
    assert "Шаблон подставлен" in r.text and "28 календарных дней" in r.text
    assert env.deps.repo.ticket(CO, s.t1.key).status == "queue"  # type: ignore[union-attr]
    r = hr.post(f"/queue/{s.t1.key}/w/tpl", data={"text": "", "q": "космос"})
    assert "Ничего не найдено" in r.text


def test_history_window_hides_confidential() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.get(f"/queue/{s.t1.key}/history")
    win = r.text.split("П2.6")[1]
    assert "История диалога" in win and "притесняет" not in win


def test_delivery_failure_is_flagged() -> None:
    env = make_env()
    s = seed(env)
    e = env.deps.repo.employee(CO, s.igor.id)
    assert e is not None
    env.deps.repo.save_employee(e.model_copy(update={"status": "disabled"}))
    r = env.login(HR).post(f"/queue/{s.t2.key}/answer", data={"text": "Оформите заявление."})
    assert "не доставлен" in r.text


def test_queue_empty_state() -> None:
    env = make_env()
    r = env.login(HR).get("/queue")
    assert "В очереди пусто" in r.text


# ----- П3 -----------------------------------------------------------------------------


def test_kb_add_validation_and_list() -> None:
    env = make_env()
    hr = env.login(HR)
    assert "База ответов пока пуста" in hr.get("/kb").text
    r = hr.post("/kb", data={"question": "Можно ли из другой страны?", "answer": "", "basis": ""})
    assert "Напишите ответ" in r.text
    r = hr.post("/kb", data={"question": "Можно?", "answer": "Нет.", "basis": ""})
    assert "Укажите статью" in r.text
    r = hr.post("/kb", data={"question": "Можно?", "answer": "Нет.", "basis": "п. 3.2 Правил"})
    assert "Ответ добавлен в базу" in r.text and "Можно?" in r.text
    r = hr.post("/kb", data={"question": "q" * 301, "answer": "a", "basis": "b"})
    assert "не длиннее 300" in r.text
    r = hr.post("/kb", data={"question": "q", "answer": "a" * 901, "basis": "b"})
    assert "не длиннее 900" in r.text


def test_kb_edit_keeps_history_and_archive_has_undo_10s() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post(
        f"/kb/{s.kb_id}/edit",
        data={
            "question": "Сколько отпуск",
            "answer": "31 день.",
            "basis": "п. 2.1",
            "audience": "all",
        },
    )
    assert "Ответ обновлён" in r.text
    r = hr.get(f"/kb/{s.kb_id}/edit")
    assert "исправил(а) ответ" in r.text
    r = hr.post(f"/kb/{s.kb_id}/archive")
    assert "Отменить — 10 с" in r.text
    assert env.deps.kb.find_same(CO, "Сколько отпуск") is None  # архив бот не использует
    uid = [d["id"] for d in env.store.all("pundo", CO)][0]
    r = hr.post(f"/undo/{uid}", data={"back": "/kb"})
    assert "Действие отменено" in r.text
    assert env.deps.kb.find_same(CO, "Сколько отпуск") is not None


def test_undo_window_closes_after_10_seconds() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(f"/kb/{s.kb_id}/archive")
    uid = [d["id"] for d in env.store.all("pundo", CO)][0]
    env.clock.advance(seconds=11)
    r = hr.post(f"/undo/{uid}", data={"back": "/kb"})
    assert "вышло" in r.text
    assert env.deps.repo.kb_entries(CO)[0].archived


def test_undo_back_is_not_open_redirect() -> None:
    env = make_env()
    r = env.login(HR).post("/undo/nope", data={"back": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_law_change_marks_affected_and_review_flow() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post("/kb/law-change", data={"article": "115"})
    assert "Помечено «проверить»: 1" in r.text
    assert env.deps.kbadmin.needs_review(CO)[0].id == s.kb_id
    assert "нужно проверить" in hr.get("/kb").text
    r = hr.get("/kb/review")
    assert "Пересмотр ответа 1 из 1" in r.text
    r = hr.post(f"/kb/review/{s.kb_id}", data={"decision": "keep"})
    assert "Пересмотр завершён" in r.text and not env.deps.kbadmin.needs_review(CO)
    assert hr.post("/kb/law-change", data={"article": ""}).text.count("Укажите номер статьи") == 1


def test_article_number_match_is_exact() -> None:
    env = make_env()
    seed(env)
    assert env.deps.kbadmin.mark_law_change(CO, "11", env.clock.now) == 0  # не ст. 115


def test_kb_search_and_empty_search() -> None:
    env = make_env()
    seed(env)
    hr = env.login(HR)
    assert "Сколько дней отпуска" in hr.get("/kb?q=отпуска").text
    assert "Ничего не найдено" in hr.get("/kb?q=космонавт").text


def test_unknown_kb_entry_is_404(hr: TestClient) -> None:
    assert hr.get("/kb/999/edit").status_code == 404
    assert hr.post("/kb/999/archive").status_code == 404


def test_ticket_model_unchanged() -> None:
    assert Ticket.model_fields["key"].is_required()
    assert Env is not None
