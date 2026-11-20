"""Изоляция компаний (SPEC-09 §9): чужое недоступно через панель, бот и базу ответов; 0 утечек.

Компания «zhichkin» наполнена приметными словами; HR компании «northwind» (с собственными
данными) обходит все страницы с чужими идентификаторами. Ни одно слово чужой компании не должно
появиться в ответах, чужие данные не должны измениться.
"""

from __future__ import annotations

import re
from typing import Any

from hrgid.flow.cache import cache_key

from .conftest import ADMIN, CO, HR, OTHER, OTHER_HR, Env, make_env
from .seed import seed
from .test_docs_check import approve
from .test_smoke import GET_URLS, fill

MARKERS = [
    "Ольга Смирнова",
    "Игорь Петров",
    "перенести отпуск",
    "притесняет",
    "Сколько дней отпуска положено",
    "Правила студии",
    "отгул за работу в субботу",
    "Студия Жичкина",
]


def make_pair() -> tuple[Env, Any]:
    env = make_env()
    s = seed(env)
    approve(env, s.doc_id, 1)
    env.login(HR).post(f"/check/{s.doc_id}/1/run")
    env.login(HR).post("/announcements/send", data={"title": "Секретный приказ", "text": "Текст А"})
    # у «northwind» свои данные, чтобы страницы не были пустыми
    e = env.employee(OTHER, "Борис Северный", emp_id="n1")
    env.connect(e, 7001)
    env.ticket(e, "Вопрос северной компании")
    env.deps.kb.add(OTHER, question="Северный вопрос", answer="Северный ответ", basis="ст. 1",
                    author="Борис", now=env.clock.now)  # fmt: skip
    return env, s


def leaks(text: str) -> list[str]:
    return [m for m in MARKERS if m in text]


def test_foreign_company_pages_have_zero_leaks() -> None:
    env, s = make_pair()
    ann_id = env.deps.repo.announcements(CO)[0].id
    jn = env.deps.journal.entries(CO)[0].n
    other = env.login(OTHER_HR)
    urls = [fill(u, s) for u in GET_URLS] + [
        f"/queue?k={s.t1.key}",
        f"/queue?tab=conf&k={s.conf.key}",
        f"/queue/{s.t1.key}/history",
        f"/rating/{s.t1.key}",
        f"/announcements/{ann_id}",
        f"/announcements/{ann_id}/remind",
        f"/journal/{jn}",
        f"/check/{s.doc_id}/1/pdf",
        f"/check?doc={s.doc_id}&v=1",
        f"/docs/{s.doc_id}/1",
        f"/docs/{s.doc_id}/compare?a=1&b=1",
        f"/kb/new?from={s.t1.key}",
    ]
    found: list[tuple[str, list[str]]] = []
    for url in urls:
        r = other.get(url)
        if bad := leaks(r.text):
            found.append((url, bad))
        assert r.status_code < 500, (url, r.status_code)
    assert found == []  # 0 утечек


def test_foreign_identifiers_do_not_resolve() -> None:
    env, s = make_pair()
    other = env.login(OTHER_HR)
    ann_id = env.deps.repo.announcements(CO)[0].id
    # номера обращений и ответов у каждой компании свои (101, 1): такой номер открывает СВОЁ
    for url in (f"/queue/{s.t1.key}/history", f"/rating/{s.t1.key}", f"/kb/{s.kb_id}/edit"):
        assert leaks(other.get(url).text) == [], url
    for url in (
        f"/announcements/{ann_id}",
        f"/docs/{s.doc_id}/1",
        f"/staff/{s.olga.id}",
        f"/staff/{s.olga.id}/edit",
        f"/check/{s.doc_id}/1/pdf",
    ):
        assert other.get(url).status_code == 404, url


def test_foreign_writes_change_nothing() -> None:
    env, s = make_pair()
    other = env.login(OTHER_HR)  # у northwind — администратор
    d = env.deps
    before = {
        "tickets": [t.model_dump_json() for t in d.repo.tickets(CO)],
        "employees": [e.model_dump_json() for e in d.repo.employees(CO)],
        "kb": [e.model_dump_json() for e in d.repo.kb_entries(CO)],
        "docs": str(d.docs.versions(CO, s.doc_id)),
    }
    ann_id = d.repo.announcements(CO)[0].id
    attempts = [
        ("post", f"/queue/{s.t1.key}/answer", {"text": "Чужой ответ"}),
        ("post", f"/queue/{s.t1.key}/assign", {"to": OTHER_HR}),
        ("post", f"/queue/{s.t1.key}/savekb", {"question": "q", "answer": "a", "basis": "b"}),
        ("post", f"/kb/{s.kb_id}/edit", {"question": "взлом", "answer": "x", "basis": "y"}),
        ("post", f"/kb/{s.kb_id}/archive", {}),
        ("post", f"/kb/{s.kb_id}/restore", {}),
        ("post", f"/kb/review/{s.kb_id}", {"decision": "archive"}),
        (
            "post",
            f"/docs/{s.doc_id}/1/order",
            {"order_no": "x", "order_date": "01.11.2026", "effective_from": "02.11.2026"},
        ),
        ("post", f"/docs/{s.doc_id}/rollback", {"to": "1", "reason": "x"}),
        ("post", f"/docs/{s.doc_id}/1/delete", {}),
        ("post", f"/check/{s.doc_id}/1/run", {}),
        ("post", f"/check/{s.doc_id}/1/fix", {"item": "3.1", "text": "чужое"}),
        ("post", f"/check/{s.doc_id}/1/accept", {"item": "3.1", "comment": "x", "understood": "1"}),
        ("post", f"/staff/{s.olga.id}/disable", {"confirm": "1"}),
        ("post", f"/staff/{s.olga.id}/newcode", {}),
        ("post", f"/staff/{s.olga.id}/edit", {"hire_date": "01.01.2000", "salary": "1"}),
        ("post", f"/staff/{s.olga.id}/purge", {"reason": "x", "understood": "1"}),
        ("post", f"/staff/{s.olga.id}/enable", {}),
        ("post", f"/announcements/{ann_id}/remind", {}),
        ("post", f"/rating/{s.t1.key}", {"decision": "ok"}),
    ]
    for _method, url, data in attempts:
        r = other.post(url, data=data)
        assert (
            r.status_code in (403, 404, 409, 503) or "Нет прав" in r.text or leaks(r.text) == []
        ), (
            url,
            r.status_code,
        )
        assert leaks(r.text) == [], url
    env.clock.advance(seconds=30)
    other.get("/staff")  # докрутить отложенные действия «другой» компании
    env.login(HR).get("/staff")
    after = {
        "tickets": [t.model_dump_json() for t in d.repo.tickets(CO)],
        "employees": [e.model_dump_json() for e in d.repo.employees(CO)],
        "kb": [e.model_dump_json() for e in d.repo.kb_entries(CO)],
        "docs": str(d.docs.versions(CO, s.doc_id)),
    }
    assert after == before
    # а в «чужой» компании ничего не появилось из-за этих попыток
    assert all(e.company_id == OTHER for e in d.repo.employees(OTHER))
    assert d.repo.outbox_pending() == [] or all(
        o.company_id in (CO, OTHER) for _, _, o in d.repo.outbox_pending()
    )


def test_foreign_lists_and_counts_only_own() -> None:
    env, _ = make_pair()
    other = env.login(OTHER_HR)
    q = other.get("/queue").text
    assert "Вопрос северной компании" in q and leaks(q) == []
    assert "Северный вопрос" in other.get("/kb").text
    stf = other.get("/staff").text
    assert "Борис Северный" in stf and "Ольга" not in stf
    assert "Нортвинд" in other.get("/").text


def test_export_company_a_not_downloadable_by_other_admin() -> None:
    env, _ = make_pair()
    adm = env.login(ADMIN)
    adm.post("/settings/export", data={})
    token = env.store.all("pexport", CO)[0]["token"]
    other = env.login(OTHER_HR)
    assert other.get(f"/settings/export/{token}").status_code == 404


def test_undo_tokens_are_per_company() -> None:
    env, s = make_pair()
    env.login(HR).post(f"/kb/{s.kb_id}/archive")
    uid = env.store.all("pundo", CO)[0]["id"]
    other = env.login(OTHER_HR)
    r = other.post(f"/undo/{uid}", data={"back": "/"})
    assert "вышло" in r.text
    assert env.deps.repo.kb_entries(CO)[0].archived  # чужой «отмены» нет


def test_session_of_deleted_user_company_does_not_cross() -> None:
    env, _ = make_pair()
    other = env.login(OTHER_HR)
    token = other.cookies["hrgid_sid"]
    hr = env.anon()
    hr.cookies.set("hrgid_sid", token)
    r = hr.get("/")  # та же сессия — тот же тенант, не чужой
    assert "Нортвинд" in r.text and "Студия Жичкина" not in r.text.split("<main")[0]


def test_bot_and_kb_channels_do_not_cross_tenants() -> None:
    env, s = make_pair()
    d = env.deps
    assert d.kb.find_same(CO, "Сколько дней отпуска положено") is not None
    assert d.kb.find_same(OTHER, "Сколько дней отпуска положено") is None
    assert d.kb.ranked(OTHER, "Сколько дней отпуска положено") == []
    assert d.repo.codes_of(OTHER, s.olga.id) == [] and d.repo.employee(OTHER, s.olga.id) is None
    mine = d.repo.ticket(OTHER, s.t1.key)  # номер 101 есть и у «northwind», но это его обращение
    assert mine is not None and mine.company_id == OTHER and "перенести отпуск" not in mine.text
    # кэш ответов: ключ зависит от компании
    assert cache_key(CO, "", "v1", "q") != cache_key(OTHER, "", "v1", "q")
    # код сотрудника компании A не открывает вход в компанию B (код привязан к своей компании)
    code = d.bot_auth.issue_code(CO, s.olga.id, env.clock.now)
    res = d.bot_auth.check_code(1, code, env.clock.now)
    assert res.employee is not None and res.employee.company_id == CO


def test_no_company_id_accepted_from_request() -> None:
    """Компания берётся только из сессии: параметры запроса с чужим company_id игнорируются."""
    env, s = make_pair()
    other = env.login(OTHER_HR)
    r = other.get(f"/queue?company_id={CO}&company={CO}&tenant_id={CO}")
    assert leaks(r.text) == []
    r = other.post("/kb", data={"question": "Моя", "answer": "а", "basis": "б", "company_id": CO})
    assert any(e.question == "Моя" for e in env.deps.repo.kb_entries(OTHER))
    assert not any(e.question == "Моя" for e in env.deps.repo.kb_entries(CO))
    assert re.search("company", "".join(str(r.url))) is None
