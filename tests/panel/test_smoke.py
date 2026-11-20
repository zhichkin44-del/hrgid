"""Каждый раздел и окно открывается (на живых данных) и не падает; для обеих ролей."""

from __future__ import annotations

import pytest

from .conftest import ADMIN, HR, Env, make_env
from .seed import Seed, seed

GET_URLS = [
    "/",
    "/loading",
    "/queue",
    "/queue?tab=mine",
    "/queue?tab=conf",
    "/queue?tab=overdue",
    "/queue?tab=common",
    "/kb",
    "/kb?tab=archive",
    "/kb?tab=review",
    "/kb?q=отпуск",
    "/kb/new",
    "/kb/{kb}/edit",
    "/kb/{kb}/archive",
    "/kb/affected",
    "/docs",
    "/docs?w=upload",
    "/docs/{doc}/1",
    "/docs/{doc}/1/order",
    "/docs/{doc}/compare",
    "/docs/{doc}/1/delete",
    "/check",
    "/check?all=1",
    "/check/{doc}/1/fix?item=2.1",
    "/staff",
    "/staff?w=invite",
    "/staff?w=import1",
    "/staff?w=codes",
    "/staff/groups/list",
    "/staff/{e1}",
    "/staff/{e1}/edit",
    "/staff/{e1}/newcode",
    "/staff/{e1}/disable",
    "/staff/{e1}/purge",
    "/announcements",
    "/announcements/new",
    "/analytics",
    "/analytics/topic?name=Отпуск",
    "/costs",
    "/costs/limit",
    "/costs/ask",
    "/settings",
    "/settings/model",
    "/settings/export",
    "/settings/delete",
    "/journal",
    "/journal?tab=logins",
    "/journal?w=filters",
    "/journal?w=export",
    "/logout",
]


def fill(url: str, s: Seed) -> str:
    return url.format(kb=s.kb_id, doc=s.doc_id, e1=s.olga.id)


@pytest.mark.parametrize("who", [ADMIN, HR])
def test_every_page_opens(who: str) -> None:
    env = make_env()
    s = seed(env)
    c = env.login(who)
    # проверка на ТК, чтобы открылись разборы пунктов
    env.login(ADMIN).post(f"/check/{s.doc_id}/1/run")
    bad = []
    for url in GET_URLS:
        r = c.get(fill(url, s))
        if r.status_code not in (200, 403):
            bad.append((url, r.status_code))
        if "Traceback" in r.text or "UndefinedError" in r.text:
            bad.append((url, "template"))
    assert not bad, bad


def test_mobile_menu_and_brand_present(env: Env, admin: object) -> None:
    c = env.login(HR)
    r = c.get("/")
    assert "HRgid" in r.text and 'name="viewport"' in r.text
