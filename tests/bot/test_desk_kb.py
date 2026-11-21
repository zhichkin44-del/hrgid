from __future__ import annotations

from datetime import timedelta

import pytest

from hrgid.bot.desk import DeskError, HrDesk, KbParams
from hrgid.bot.kb import KbError, KbService, similarity
from hrgid.bot.models import Employee
from hrgid.bot.repo import Repo

from .conftest import COMPANY, HR_CHAT, NOW, OLGA_TG, make_employee, msk


def _linked(repo: Repo) -> Employee:
    return make_employee(repo, telegram_id=OLGA_TG, consent_at=NOW)


def test_create_notifies_hr_and_sets_sla(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    t = desk.create(e, "Можно ли взять отпуск за свой счёт на две недели?", NOW)
    assert t.key == "101" and t.label == "№101" and t.due_at == msk(2026, 11, 19, 10, 40)
    [(company, _, out)] = repo.outbox_pending()
    assert company == COMPANY and out.chat_id == HR_CHAT
    assert "Ольга Смирнова" in out.text and "завтра до 10:40" in out.text
    assert desk.create(e, "второй", NOW).key == "102"


def test_confidential_hides_name_everywhere(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    t = desk.create(e, "Руководитель пишет по ночам", NOW, confidential=True)
    assert t.label == "К-1" and t.kind == "confidential"
    out = repo.outbox_pending()[0][2]
    assert "Ольга" not in out.text and "конфиденциально" in out.text
    v = desk.view(t, NOW)
    assert v.employee_name is None and v.confidential
    # after the reveal, the name is visible
    assert desk.view(desk.reveal(t), NOW).employee_name == "Ольга Смирнова"
    # analytics: the text of confidential requests does not get in
    desk.create(e, "обычный вопрос", NOW)
    rows = {r["key"]: r for r in desk.analytics_rows(COMPANY)}
    assert rows["K1"]["text"] == "" and rows["101"]["text"] == "обычный вопрос"


def test_confidential_never_goes_to_kb(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    t = desk.create(e, "жалоба", NOW, confidential=True)
    with pytest.raises(DeskError):
        desk.answer(COMPANY, t.key, "Анна", "ответ", NOW, save_to_kb=KbParams("в", "о", "ст. 1"))
    assert repo.kb_entries(COMPANY) == []


def test_answer_goes_to_employee_chat_and_rating_closes(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    t = desk.create(e, "Компенсирует ли Студия абонемент?", NOW)
    t2, entry = desk.answer(COMPANY, t.key, "Анна Воронова", "Не предусмотрено.", NOW)
    assert t2.status == "answered" and entry is None and t2.assignee == "Анна Воронова"
    pushes = [o for _, _, o in repo.outbox_pending() if o.chat_id == OLGA_TG]
    assert len(pushes) == 1 and "Ответ HR · обращение №101" in pushes[0].text
    assert [b.data for b in pushes[0].rows[0]] == ["hrr:up:101", "hrr:down:101"]
    closed = desk.rate(t2, "up")
    assert closed.status == "closed" and closed.rating == "up"


def test_answer_requires_text_and_kb_fields(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    t = desk.create(e, "вопрос", NOW)
    with pytest.raises(DeskError):
        desk.answer(COMPANY, t.key, "Анна", "  ", NOW)
    with pytest.raises(KbError) as ei:
        desk.answer(COMPANY, t.key, "Анна", "ответ", NOW, save_to_kb=KbParams("в", "о", " "))
    assert ei.value.field == "basis"
    assert desk.get(COMPANY, t.key).status == "queue"  # nothing sent


def test_answer_to_unlinked_employee_marks_delivery_failed(repo: Repo, desk: HrDesk) -> None:
    e = make_employee(repo, telegram_id=OLGA_TG, consent_at=NOW)
    t = desk.create(e, "вопрос", NOW)
    repo.save_employee(e.model_copy(update={"telegram_id": None}))
    t2, _ = desk.answer(COMPANY, t.key, "Анна", "ответ", NOW)
    assert t2.delivery_failed and desk.view(t2, NOW).delivery_failed


def test_save_to_kb_and_same_question_search(repo: Repo, desk: HrDesk, kb: KbService) -> None:
    e = _linked(repo)
    t = desk.create(e, "Можно ли перенести отпуск с декабря на январь?", NOW)
    answer = "Можно по заявлению и с согласия работодателя."
    _, entry = desk.answer(
        COMPANY, t.key, "Анна", answer, NOW, save_to_kb=KbParams(t.text, answer, "ст. 124 ТК РФ")
    )
    assert entry is not None and entry.source_ticket == "101"
    found = kb.find_same(COMPANY, "Можно перенести отпуск с декабря на январь?")
    assert found is not None and found.basis == "ст. 124 ТК РФ"
    assert kb.find_same(COMPANY, "Сколько дней отпуска положено в год?") is None
    assert kb.find_same("other", "Можно перенести отпуск с декабря на январь?") is None
    assert similarity("отпуск в январе", "отпуск") > 0


def test_withdraw_append_and_overdue(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    t = desk.create(e, "вопрос про отгул", NOW)
    t = desk.append(t, "добавлю: за субботу", NOW)
    assert len(t.messages) == 2
    assert not desk.list_tickets(COMPANY, "overdue", NOW)
    late = NOW + timedelta(days=2)
    assert [v.key for v in desk.list_tickets(COMPANY, "overdue", late)] == ["101"]
    desk.assign(COMPANY, "101", "Анна")
    assert [v.key for v in desk.list_tickets(COMPANY, "mine", NOW, "Анна")] == ["101"]
    assert desk.list_tickets(COMPANY, "common", NOW) == []
    t = desk.withdraw(desk.get(COMPANY, "101"))
    assert t.status == "withdrawn" and desk.list_tickets(COMPANY, "all", NOW) == []


def test_delete_with_undo_window(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    t = desk.create(e, "жалоба", NOW, confidential=True)
    t = desk.request_delete(t, NOW)
    assert desk.list_tickets(COMPANY, "conf", NOW) == []  # HR no longer sees it
    back = desk.undo_delete(t, NOW + timedelta(seconds=5))
    assert back is not None and back.status == "queue"
    t = desk.request_delete(back, NOW)
    assert desk.undo_delete(t, NOW + timedelta(seconds=11)) is None
    assert desk.finalize_company(COMPANY, NOW + timedelta(seconds=11)) == 1
    done = desk.get(COMPANY, t.key)
    assert done.status == "deleted" and done.text == ""


def test_purge_employee_data(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    desk.create(e, "мои личные данные", NOW)
    desk.purge_employee(COMPANY, e.id)
    emp = repo.employee(COMPANY, e.id)
    assert emp is not None and emp.full_name == "Данные удалены" and emp.telegram_id is None
    assert repo.tickets(COMPANY)[0].text == ""


def test_announcements_and_unread(repo: Repo, desk: HrDesk) -> None:
    a = _linked(repo)
    b = make_employee(repo, "e2", "Иван Петров", telegram_id=5002, consent_at=NOW)
    make_employee(repo, "e3", "Не вошёл")  # without Telegram, does not receive
    ann = desk.publish_announcement(
        COMPANY, "График отпусков на 2027 год", "Пишите даты", "Анна", NOW, asks_dates=True
    )
    pushes = [o for _, _, o in repo.outbox_pending()]
    assert {o.chat_id for o in pushes} == {OLGA_TG, 5002}
    assert pushes[0].rows[1][0].data == f"ann:dates:{ann.id}"
    assert desk.mark_read(COMPANY, ann.id, a.id, NOW)
    assert desk.unread(COMPANY, ann.id) == [b.full_name]


def test_tenant_isolation(repo: Repo, desk: HrDesk) -> None:
    e = _linked(repo)
    desk.create(e, "вопрос компании 1", NOW)
    other = make_employee(repo, "x1", "Чужой", company="other", telegram_id=7)
    desk.create(other, "вопрос компании 2", NOW)
    assert [t.text for t in repo.tickets("other")] == ["вопрос компании 2"]
    assert [t.text for t in repo.tickets(COMPANY)] == ["вопрос компании 1"]
    assert repo.ticket("other", "101") is not None  # numbering is separate for each company
    assert [v.text for v in desk.list_tickets("other", "all", NOW)] == ["вопрос компании 2"]
