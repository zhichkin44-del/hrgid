"""Rights per the SCREENS.md §1 registry: the "section x role" matrix and the closedness of all routes without sign-in."""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from hrgid.panel.perms import ADMIN_ACTIONS, can, can_section

from .conftest import ADMIN, CO, HR, make_env
from .seed import seed
from .test_docs_check import approve

PUBLIC = ("/login", "/healthz", "/static")


def test_perm_table_matches_registry() -> None:
    for act in ADMIN_ACTIONS:
        assert can("admin", act) and not can("hr", act), act
    for act in ("ticket.answer", "kb.add", "emp.purge", "doc.upload", "tk.fix", "ann.send"):
        assert can("hr", act) and can("admin", act), act
    assert not can_section("hr", "settings") and can_section("admin", "settings")
    assert not can("stranger", "ticket.answer") and not can_section("", "queue")


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/queue",
        "/kb",
        "/docs",
        "/check",
        "/staff",
        "/announcements",
        "/analytics",
        "/costs",
        "/journal",
    ],
)
def test_sections_open_for_both_roles(url: str) -> None:
    env = make_env()
    seed(env)
    assert env.login(HR).get(url).status_code == 200
    assert env.login(ADMIN).get(url).status_code == 200


def test_settings_only_admin_everything_else_open() -> None:
    env = make_env()
    seed(env)
    hr, adm = env.login(HR), env.login(ADMIN)
    assert hr.get("/settings").status_code == 403 and adm.get("/settings").status_code == 200
    nav = hr.get("/").text
    assert 'href="/settings"' in nav  # the item exists, inside it "no rights" (П9.6)


def test_admin_only_actions_return_403_for_hr_and_change_nothing() -> None:
    env = make_env()
    s = seed(env)
    approve(env, s.doc_id, 1)
    env.login(HR).post(f"/check/{s.doc_id}/1/run")
    hr = env.login(HR)
    d = s.doc_id
    attempts = [
        ("get", f"/docs/{d}/1/order", {}),
        (
            "post",
            f"/docs/{d}/1/order",
            {"order_no": "9", "order_date": "01.11.2026", "effective_from": "02.11.2026"},
        ),
        ("get", f"/docs/{d}/rollback?to=1", {}),
        ("post", f"/docs/{d}/rollback", {"to": "1", "reason": "x"}),
        ("get", f"/docs/{d}/1/delete", {}),
        ("post", f"/docs/{d}/1/delete", {}),
        ("get", f"/check/{d}/1/accept?item=3.1", {}),
        ("post", f"/check/{d}/1/accept", {"item": "3.1", "comment": "x", "understood": "1"}),
        ("get", "/costs/limit", {}),
        ("post", "/costs/limit", {"limit": "99999", "action": "queue"}),
        ("get", "/settings", {}),
        ("post", "/settings", {}),
        ("get", "/settings/model", {}),
        ("post", "/settings/model", {"provider": "yandex"}),
        ("get", "/settings/export", {}),
        ("post", "/settings/export", {}),
        ("get", "/settings/delete", {}),
        ("post", "/settings/delete", {"exported": "1"}),
        ("post", "/settings/delete/confirm", {"name": "Студия Жичкина"}),
    ]
    for method, url, data in attempts:
        r = hr.get(url) if method == "get" else hr.post(url, data=data)
        assert r.status_code == 403, (method, url, r.status_code)
    assert env.deps.docs.version(CO, d, 1).order_no == "1"
    assert env.deps.repo.employees(CO) and env.deps.ops.tech(CO).budget_rub == 10000


def test_hr_variants_of_screens_hide_buttons_with_caption() -> None:
    env = make_env()
    s = seed(env)
    approve(env, s.doc_id, 1)
    hr = env.login(HR)
    docs = hr.get("/docs").text.split("<main")[1]
    assert (
        "Указать приказ" not in docs
        and "Откатить" not in docs
        and "доступно администратору" in docs.lower()
    )
    env.login(HR).post(f"/check/{s.doc_id}/1/run")
    chk = hr.get(f"/check?doc={s.doc_id}&v=1").text
    assert "/accept?item" not in chk and "Доступно администратору" in chk
    assert "Исправить пункт" in chk
    adm = env.login(ADMIN)
    assert "Почему принимаем с риском" in adm.get(f"/check?doc={s.doc_id}&v=1").text


def test_every_route_requires_login() -> None:
    env = make_env()
    seed(env)
    anon = env.anon()
    checked = 0
    for route in env.app.routes:
        if not isinstance(route, APIRoute) or route.path.startswith(PUBLIC):
            continue
        url = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}:
            r = anon.request(method, url, data={}, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"].startswith("/login"), (
                method,
                route.path,
                r.status_code,
            )
            checked += 1
    assert checked > 80  # went through the whole list of routes


def test_public_routes_are_only_login_pages() -> None:
    env = make_env()
    public = {
        r.path for r in env.app.routes if isinstance(r, APIRoute) and r.path.startswith(PUBLIC)
    }
    assert public == {
        "/login", "/login/expired", "/login/resend", "/login/code", "/healthz"
    }  # fmt: skip
