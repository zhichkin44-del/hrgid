"""П4 «Документы» и П5 «Проверка на ТК»: версии, приказ, откат, удаление, риски, права."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import ADMIN, CO, HR, Env, make_env
from .seed import RULES, docx_bytes, seed


def upload(c: TestClient, lines: list[str], doc_id: str = "", title: str = "Правила") -> str:
    r = c.post(
        "/docs/upload",
        data={"title": title, "doc_id": doc_id},
        files={"file": ("rules.docx", docx_bytes(lines), "application/octet-stream")},
    )
    return r.text


def test_docs_empty_state() -> None:
    env = make_env()
    assert "Документов пока нет" in env.login(HR).get("/docs").text


def test_upload_parse_then_order_by_admin() -> None:
    env = make_env()
    hr = env.login(HR)
    text = upload(hr, RULES)
    assert "прочитано" in text
    doc = env.deps.docs.documents(CO)[0]["doc_id"]
    v = env.deps.docs.version(CO, doc, 1)
    assert len(v.items) >= 6 and not v.applicable(env.clock.now.date())
    adm = env.login(ADMIN)
    r = adm.get(f"/docs/{doc}/1/order")
    assert "Приказ и дата начала" in r.text
    r = adm.post(
        f"/docs/{doc}/1/order", data={"order_no": "", "order_date": "", "effective_from": ""}
    )
    assert "Укажите номер приказа" in r.text and "Укажите дату приказа" in r.text
    r = adm.post(
        f"/docs/{doc}/1/order",
        data={"order_no": "12", "order_date": "15.11.2026", "effective_from": "01.11.2026"},
    )
    assert "раньше даты приказа" in r.text
    r = adm.post(
        f"/docs/{doc}/1/order",
        data={"order_no": "12", "order_date": "15.11.2026", "effective_from": "16.11.2026"},
    )
    assert "Приказ записан" in r.text and "действует" in r.text
    assert env.deps.docs.version(CO, doc, 1).applicable(env.clock.now.date())


def test_bad_file_is_rejected_with_message() -> None:
    env = make_env()
    hr = env.login(HR)
    r = hr.post("/docs/upload", files={"file": ("x.txt", b"hello", "text/plain")})
    assert "Файл не принят" in r.text
    r = hr.post("/docs/upload", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert "Файл не принят" in r.text
    assert not env.deps.docs.documents(CO)
    assert "Выберите файл" in hr.post("/docs/upload", data={"title": "x"}).text


def test_hr_has_no_order_rollback_delete() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    page = hr.get("/docs").text
    assert "доступно администратору" in page and "/order" not in page.split("<main")[1]
    for url in (
        f"/docs/{s.doc_id}/1/order",
        f"/docs/{s.doc_id}/rollback?to=1",
        f"/docs/{s.doc_id}/1/delete",
    ):
        assert hr.get(url).status_code == 403, url
    r = hr.post(
        f"/docs/{s.doc_id}/1/order",
        data={"order_no": "1", "order_date": "01.11.2026", "effective_from": "02.11.2026"},
    )
    assert r.status_code == 403
    assert not env.deps.docs.version(CO, s.doc_id, 1).order_no


def approve(env: Env, doc: str, number: int, no: str = "1", frm: str = "01.11.2026") -> None:
    adm = env.login(ADMIN)
    r = adm.post(
        f"/docs/{doc}/{number}/order",
        data={"order_no": no, "order_date": "01.11.2026", "effective_from": frm},
    )
    assert "Приказ записан" in r.text


def test_new_version_compare_rollback_and_active_delete_denied() -> None:
    env = make_env()
    s = seed(env)
    approve(env, s.doc_id, 1)
    hr = env.login(HR)
    new = [x.replace("один раз в месяц", "два раза в месяц") for x in RULES]
    upload(hr, new, doc_id=s.doc_id)
    approve(env, s.doc_id, 2, "2", "10.11.2026")
    r = hr.get(f"/docs/{s.doc_id}/compare?a=1&b=2")
    assert "Сравнение версий 1 и 2" in r.text and "два раза в месяц" in r.text
    adm = env.login(ADMIN)
    r = adm.get(f"/docs/{s.doc_id}/2/delete")
    assert "удалить нельзя" in r.text  # действует
    assert adm.post(f"/docs/{s.doc_id}/2/delete").status_code == 409
    r = adm.post(f"/docs/{s.doc_id}/rollback", data={"to": "1", "reason": ""})
    assert "Укажите причину" in r.text
    r = adm.post(f"/docs/{s.doc_id}/rollback", data={"to": "1", "reason": "ошибка в версии 2"})
    assert "Откат выполнен" in r.text
    active = env.deps.docs.active_version(CO, s.doc_id, env.clock.now.date())
    assert active is not None and active.number == 1


def test_delete_version_has_10_second_undo_and_executes_after() -> None:
    env = make_env()
    s = seed(env)
    approve(env, s.doc_id, 1)
    upload(env.login(HR), RULES, doc_id=s.doc_id)  # версия 2 — проект, не действует
    adm = env.login(ADMIN)
    r = adm.post(f"/docs/{s.doc_id}/2/delete")
    assert "Отменить — 10 с" in r.text and len(env.deps.docs.versions(CO, s.doc_id)) == 2
    uid = env.store.all("pundo", CO)[0]["id"]
    assert "Действие отменено" in adm.post(f"/undo/{uid}", data={"back": "/docs"}).text
    adm.post(f"/docs/{s.doc_id}/2/delete")
    env.clock.advance(seconds=12)
    adm.get("/docs")  # любой запрос докручивает окно отмены
    assert [v.number for v in env.deps.docs.versions(CO, s.doc_id)] == [1]


# ----- П5 ------------------------------------------------------------------------------


def test_check_empty_and_unchecked() -> None:
    env = make_env()
    hr = env.login(HR)
    assert "Пока нечего проверять" in hr.get("/check").text
    s = seed(env)
    r = hr.get("/check")
    assert "ещё не проверена" in r.text
    r = hr.post(f"/check/{s.doc_id}/1/run")
    assert "Проверка готова" in r.text and "Риск нарушения" in r.text


def test_check_finds_planted_violations_and_suggests_fix() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(f"/check/{s.doc_id}/1/run")
    assert env.deps.checker is not None
    rep = env.deps.checker.report(CO, s.doc_id, 1)
    assert rep is not None and {i.number for i in rep.risks} == {"3.1"}  # выплата раз в месяц
    page = hr.get(f"/check?doc={s.doc_id}&v=1").text
    assert "ст. 136" in page and "Предложенная формулировка" in page
    assert "Принять с риском — доступно администратору" in page


def test_fix_item_creates_new_version_and_rechecks() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(f"/check/{s.doc_id}/1/run")
    r = hr.get(f"/check/{s.doc_id}/1/fix?item=3.1")
    assert "Исправить пункт 3.1" in r.text
    r = hr.post(f"/check/{s.doc_id}/1/fix", data={"item": "3.1", "text": ""})
    assert "Напишите итоговый текст" in r.text
    r = hr.post(
        f"/check/{s.doc_id}/1/fix",
        data={
            "item": "3.1",
            "text": "Заработная плата выплачивается два раза в месяц: 25 числа и 10 числа.",
        },
    )
    assert "Создана версия 2" in r.text
    rep2 = env.deps.checker.report(CO, s.doc_id, 2)  # type: ignore[union-attr]
    assert rep2 is not None and not rep2.risks


def test_accept_risk_admin_only_with_comment_and_understood() -> None:
    env = make_env()
    s = seed(env)
    env.login(HR).post(f"/check/{s.doc_id}/1/run")
    hr = env.login(HR)
    assert hr.get(f"/check/{s.doc_id}/1/accept?item=3.1").status_code == 403
    assert (
        hr.post(f"/check/{s.doc_id}/1/accept", data={"item": "3.1", "comment": "x"}).status_code
        == 403
    )
    adm = env.login(ADMIN)
    r = adm.post(
        f"/check/{s.doc_id}/1/accept", data={"item": "3.1", "comment": "", "understood": "1"}
    )
    assert "Напишите причину" in r.text
    r = adm.post(
        f"/check/{s.doc_id}/1/accept", data={"item": "3.1", "comment": "договор", "understood": ""}
    )
    assert "понимаю последствия" in r.text
    r = adm.post(
        f"/check/{s.doc_id}/1/accept",
        data={"item": "3.1", "comment": "согласовано с юристом", "understood": "1"},
    )
    assert "Риск принят" in r.text and "согласовано с юристом" in r.text
    entry = [e for e in env.deps.journal.entries(CO) if e.action == "tk.accept_risk"]
    assert entry and entry[0].user == ADMIN


def test_pdf_report_downloads() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    assert hr.get(f"/check/{s.doc_id}/1/pdf").status_code == 404  # ещё не проверено
    hr.post(f"/check/{s.doc_id}/1/run")
    r = hr.get(f"/check/{s.doc_id}/1/pdf")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.content[:4] == b"%PDF" or b"<html" in r.content[:100].lower()


def test_check_all_items_view() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(f"/check/{s.doc_id}/1/run")
    r = hr.get(f"/check?doc={s.doc_id}&v=1&all=1")
    assert "Вернуться к рискам" in r.text and "2.1" in r.text


def test_draft_version_not_applied_and_label_shown() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(f"/check/{s.doc_id}/1/run")
    assert "проект, ещё не утверждена приказом" in hr.get(f"/check?doc={s.doc_id}&v=1").text
