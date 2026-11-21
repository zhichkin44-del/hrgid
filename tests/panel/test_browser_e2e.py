"""Six SPEC-09 §17 scenarios in a real browser (Playwright + Chrome): clicks, windows, Esc, HTMX.

Marker `browser`: needs Playwright and a browser (the built-in Chromium, or an installed Chrome/Edge).
In CI: `playwright install chromium`. The panel runs on a live server in this same process, on
in-memory storage; the bot is the engine with the Fake flow (as in test_e2e_scenarios).
"""

from __future__ import annotations

import csv
import io
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pw_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from hrgid.flow.graph import FlowAnswer, Source  # noqa: E402

from .conftest import ADMIN, CO, HR, Env, make_env  # noqa: E402
from .seed import RULES, docx_bytes  # noqa: E402
from .test_e2e_scenarios import STAFF_CSV, Bot, Pipe, text_of  # noqa: E402

pytestmark = pytest.mark.browser


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Live:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(
            uvicorn.Config(env.app, host="127.0.0.1", port=self.port, log_level="error")
        )
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.1)
        raise RuntimeError("сервер не поднялся")

    def stop(self) -> None:
        self.server.should_exit = True


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with pw_api.sync_playwright() as p:
        last: Exception | None = None
        b = None
        for kw in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
            try:
                b = p.chromium.launch(headless=True, **kw)
                break
            except Exception as e:  # no browser: the test is skipped
                last = e
        if b is None:
            pytest.skip(f"нет браузера для Playwright: {last}")
        yield b
        b.close()


@pytest.fixture()
def live() -> Iterator[Live]:
    lv = Live(make_env())
    yield lv
    lv.stop()


def new_page(browser: Any, width: int = 1440, height: int = 900) -> Any:
    ctx = browser.new_context(viewport={"width": width, "height": height}, locale="ru-RU")
    page = ctx.new_page()
    page.set_default_timeout(8000)
    return page


def login(page: Any, lv: Live, email: str) -> None:
    page.goto(lv.url + "/login")
    page.fill("input[name=email]", email)
    page.click("button[type=submit]")
    page.fill("input[name=code]", lv.env.mailer.last_code(email))
    page.click("button[type=submit]")
    page.wait_for_url(lv.url + "/")


def no_h_scroll(page: Any) -> bool:
    return bool(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))


def upload_rules(
    page: Any, lv: Live, lines: list[str] = RULES, title: str = "Правила студии"
) -> None:
    page.goto(lv.url + "/docs?w=upload")
    page.fill("input[name=title]", title)
    page.set_input_files(
        "input[type=file]",
        {"name": "rules.docx", "mimeType": "application/octet-stream", "buffer": docx_bytes(lines)},
    )
    page.get_by_role("button", name="Загрузить", exact=True).click()


def set_order(page: Any, no: str = "1") -> None:
    page.wait_for_selector("text=Приказ и дата начала")
    dlg = page.locator(".dlg")
    dlg.locator("input[name=order_no]").fill(no)
    dlg.locator("input[name=order_date]").fill("01.11.2026")
    dlg.locator("input[name=effective_from]").fill("01.11.2026")
    dlg.get_by_role("button", name="Сохранить приказ").click()
    page.wait_for_selector("text=Приказ записан")


# ----------------------------------------------------------------------------------------
# 1. New company: administrator -> document -> Labor Code check -> import 3 -> question in Telegram
# ----------------------------------------------------------------------------------------


def test_browser_scenario_1_new_company(browser: Any, live: Live) -> None:
    page = new_page(browser)
    login(page, live, ADMIN)
    assert page.locator("text=Начало работы").count() == 1
    upload_rules(page, live)
    set_order(page)
    page.goto(live.url + "/check")
    page.get_by_role("button", name="Проверить версию на ТК").click()
    page.wait_for_selector("text=Риск нарушения")
    assert page.locator(".pill.bad").count() >= 1
    # 1C import, step by step
    page.goto(live.url + "/staff?w=import1")
    page.set_input_files(
        "input[type=file]", {"name": "zup.csv", "mimeType": "text/csv", "buffer": STAFF_CSV}
    )
    page.get_by_role("button", name="Дальше").click()
    page.wait_for_selector("text=сопоставление колонок")
    page.get_by_role("button", name="Дальше").click()
    page.wait_for_selector("text=предпросмотр")
    page.get_by_role("button", name="Импортировать 3 строк").click()
    page.wait_for_selector("text=Импорт — итог")
    page.get_by_role("link", name="Выдать коды новым").click()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Сформировать").click()
    rows = list(
        csv.reader(
            io.StringIO(Path(dl.value.path()).read_text(encoding="utf-8-sig")), delimiter=";"
        )
    )
    codes = {r[0]: r[2] for r in rows[1:]}
    assert len(codes) == 3
    pipe = Pipe()
    pipe.on("отпуск", FlowAnswer("answered", "31 календарный день.", "rules",
                                 [Source(1, "Правила студии, п. 2.1", "2.1", "31 календарный день", CO)]))  # fmt: skip
    bot = Bot(live.env, pipe)
    bot.login(7001, codes["Козлов Дмитрий"])
    assert "31 календарный день" in text_of(bot.say(7001, "Сколько дней отпуска?"))
    page.goto(live.url + "/staff")
    assert page.locator("text=Подключён").count() == 1
    # the same path on a phone: no horizontal scrolling
    page.set_viewport_size({"width": 375, "height": 812})
    for path in ("/", "/staff", "/docs", "/check"):
        page.goto(live.url + path)
        assert no_h_scroll(page), path


# ----------------------------------------------------------------------------------------
# 2. Question with no answer -> П1 -> П2 HR reply -> to the base -> the same question is answered from the base
# ----------------------------------------------------------------------------------------


def test_browser_scenario_2_hr_answer_to_kb(browser: Any, live: Live) -> None:
    env = live.env
    for name in ("Козлов Дмитрий", "Лебедева Анастасия"):
        env.deps.staff.invite(CO, name, "01.01.2024", env.clock.now)
    codes = {e.full_name: env.deps.bot_auth.issue_code(CO, e.id, env.clock.now)
             for e in env.deps.repo.employees(CO)}  # fmt: skip
    pipe = Pipe()
    bot = Bot(env, pipe)
    bot.login(7001, codes["Козлов Дмитрий"])
    bot.login(7002, codes["Лебедева Анастасия"])
    out = bot.say(7001, "Можно ли работать из другого города две недели?")
    bot.tap(7001, out, "Передать HR")
    calls = len(pipe.calls)
    page = new_page(browser)
    login(page, live, HR)
    page.evaluate("window.__marker = 1")
    assert page.locator("text=Можно ли работать из другого города").count() >= 1  # П1
    page.get_by_role("link", name="Очередь").first.click()  # HTMX navigation without a reload
    page.wait_for_url(live.url + "/queue")
    assert page.evaluate("window.__marker") == 1
    page.fill("textarea[name=text]", "Можно до 14 дней по согласованию с руководителем.")
    page.get_by_role("button", name="Отправить ответ").click()
    page.wait_for_selector("text=Отправить ответ?")
    # base fields appear on a checkbox (Alpine)
    assert not page.locator("select[name=basis]").is_visible()
    page.get_by_text("Сохранить ответ в базу").click()
    page.wait_for_selector("select[name=basis]", state="visible")
    page.select_option("select[name=basis]", "ст. 124 ТК РФ")
    page.get_by_role("button", name="Отправить", exact=True).click()
    page.wait_for_selector("text=Ответ отправлен")
    got = bot.push(7001)
    assert any("Можно до 14 дней" in o.text for o in got)
    out = bot.say(7002, "Можно ли работать из другого города две недели подряд?")
    assert "Ответ HR из базы" in text_of(out) and len(pipe.calls) == calls
    assert "anna@" not in text_of(out) and "Анна Воронова" in text_of(out)
    page.goto(live.url + "/kb")
    assert page.locator("text=ст. 124 ТК РФ").count() >= 1


# ----------------------------------------------------------------------------------------
# 3. Risk -> fix -> new version -> re-check -> the bot uses the new version
# ----------------------------------------------------------------------------------------


def test_browser_scenario_3_fix_risk_bot_uses_new_version(browser: Any, live: Live) -> None:
    env = live.env
    page = new_page(browser)
    login(page, live, ADMIN)
    upload_rules(page, live)
    set_order(page, "1")
    page.goto(live.url + "/check")
    page.get_by_role("button", name="Проверить версию на ТК").click()
    page.wait_for_selector("text=Риск нарушения")
    emp, _ = env.deps.staff.invite(CO, "Козлов Дмитрий", "01.01.2024", env.clock.now)
    bot = Bot(env, Pipe())
    bot.login(7001, env.deps.bot_auth.issue_code(CO, emp.id, env.clock.now))
    v1 = bot.tap(7001, bot.say(7001, "/rules"), "Правила студии")
    assert "один раз в месяц" in text_of(v1)
    page.get_by_role("link", name="Исправить пункт").first.click()
    page.wait_for_selector("text=Исправить пункт 3.1")
    page.fill(
        "textarea[name=text]", "Заработная плата выплачивается два раза в месяц: 25 и 10 числа."
    )
    page.get_by_role("button", name="Сохранить версию и проверить заново").click()
    page.wait_for_selector("text=Создана версия 2")
    assert page.locator("text=Рисков нет").count() == 1
    assert "один раз в месяц" in text_of(bot.tap(7001, bot.say(7001, "/rules"), "Правила студии"))
    page.goto(live.url + "/docs/d1/2/order")
    set_order(page, "2")
    v2 = bot.tap(7001, bot.say(7001, "/rules"), "Правила студии")
    assert "два раза в месяц" in text_of(v2)


# ----------------------------------------------------------------------------------------
# 4. Change of a law article -> answers marked "check"; windows, Esc, cancel
# ----------------------------------------------------------------------------------------


def test_browser_scenario_4_law_change_and_window_controls(browser: Any, live: Live) -> None:
    env = live.env
    env.deps.kb.add(CO, question="Сколько дней отпуска", answer="28 дней.", basis="ст. 115 ТК РФ",
                    author="Анна", now=env.clock.now)  # fmt: skip
    page = new_page(browser)
    login(page, live, HR)
    page.goto(live.url + "/kb")
    page.get_by_role("link", name="Затронутые ответы").click()
    page.wait_for_selector("text=Затронутые ответы >> nth=1")
    page.fill("input[name=article]", "115")
    page.get_by_role("button", name="Пометить ответы").click()
    page.wait_for_selector("text=Помечено «проверить»: 1")
    assert page.locator(".pill.bad", has_text="Проверить").count() == 1
    page.get_by_role("link", name="Пересмотреть →").click()
    page.wait_for_selector("text=Пересмотр ответа 1 из 1")
    # Esc closes the window and returns to the screen it was opened from
    page.keyboard.press("Escape")
    page.wait_for_selector(".ov", state="detached")
    page.goto(live.url + "/kb/review")
    page.get_by_role("button", name="Оставить").click()
    page.wait_for_selector("text=Пересмотр завершён")
    assert page.locator(".pill.bad", has_text="Проверить").count() == 0
    # the "To archive" window -> "Cancel" does not change data; a click on the overlay also closes it
    page.goto(live.url + "/kb/1/archive")
    page.get_by_role("link", name="Отмена").click()
    page.wait_for_url(live.url + "/kb")
    assert not env.deps.repo.kb_entries(CO)[0].archived
    page.goto(live.url + "/kb/1/archive")
    page.mouse.click(5, 5)
    page.wait_for_selector(".ov", state="detached")
    # archive with a 10-second cancellation
    page.goto(live.url + "/kb/1/archive")
    page.get_by_role("button", name="В архив").click()
    page.get_by_role("button", name="Отменить — 10 с").click()
    page.wait_for_selector("text=Действие отменено")
    assert not env.deps.repo.kb_entries(CO)[0].archived
    for width in (375, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(live.url + "/kb")
        assert no_h_scroll(page), width


# ----------------------------------------------------------------------------------------
# 5. Budget exhausted -> action from П9/П8 -> warning in П1
# ----------------------------------------------------------------------------------------


def test_browser_scenario_5_budget(browser: Any, live: Live) -> None:
    env = live.env
    env.deps.staff.invite(CO, "Козлов Дмитрий", "01.01.2024", env.clock.now)
    page = new_page(browser)
    login(page, live, ADMIN)
    assert page.locator("text=Бюджет месяца исчерпан").count() == 0
    page.goto(live.url + "/costs")
    page.get_by_role("link", name="Изменить лимит").click()
    page.fill("input[name=limit]", "500")
    page.get_by_role("button", name="Сохранить").click()
    page.wait_for_selector("text=не меньше 1 000")  # field error in the same window
    page.fill("input[name=limit]", "1000")
    page.select_option("select[name=action]", "refuse")
    page.get_by_role("button", name="Сохранить").click()
    page.wait_for_selector("text=Лимит изменён")
    env.deps.ops.log_question(CO, masked_question="расход", cost_rub=1000.0, now=env.clock.now)
    page.goto(live.url + "/")
    page.wait_for_selector("text=Бюджет месяца исчерпан")
    assert page.locator("text=вежливо отказывает").count() == 1
    page.get_by_role("link", name="Открыть настройки →").click()
    page.wait_for_url(live.url + "/settings")
    assert "Только для администратора" in page.inner_text("main")
    # HR does not see the settings
    hr_page = new_page(browser)
    login(hr_page, live, HR)
    hr_page.goto(live.url + "/settings")
    assert hr_page.locator("text=только для администратора").count() >= 1
    assert hr_page.locator("text=Попросить администратора").count() >= 1


# ----------------------------------------------------------------------------------------
# 6. Dismissal -> deactivation (10 s cancel) -> the bot does not answer, history is kept
# ----------------------------------------------------------------------------------------


def test_browser_scenario_6_dismissal(browser: Any, live: Live) -> None:
    env = live.env
    emp, _ = env.deps.staff.invite(CO, "Никитин Сергей", "20.01.2023", env.clock.now)
    pipe = Pipe()
    bot = Bot(env, pipe)
    bot.login(7003, env.deps.bot_auth.issue_code(CO, emp.id, env.clock.now))
    out = bot.tap(7003, bot.say(7003, "Позвать HR"), "Обычный вопрос к HR")
    bot.say(7003, "Оформите мне справку о доходах")
    page = new_page(browser)
    login(page, live, HR)
    page.goto(live.url + "/staff")
    page.get_by_role("link", name="Никитин Сергей").click()
    page.get_by_role("link", name="Отключить").click()
    page.wait_for_selector("text=Отключить доступ?")
    page.get_by_role("button", name="Отключить доступ").click()
    page.wait_for_selector(
        "text=больше не работает"
    )  # field error: cannot proceed without a checkbox
    page.get_by_text("Сотрудник больше не работает в компании").click()
    page.get_by_role("button", name="Отключить доступ").click()
    page.wait_for_selector("text=Доступ отключён")
    page.get_by_role("button", name="Отменить — 10 с").click()
    page.wait_for_selector("text=Действие отменено")
    assert env.deps.repo.employee(CO, emp.id).status == "active"  # type: ignore[union-attr]
    page.goto(live.url + "/staff")
    page.get_by_role("link", name="Никитин Сергей").click()
    page.get_by_role("link", name="Отключить").click()
    page.get_by_text("Сотрудник больше не работает в компании").click()
    page.get_by_role("button", name="Отключить доступ").click()
    page.wait_for_selector("text=Доступ отключён")
    calls = len(pipe.calls)
    out = bot.say(7003, "Сколько дней отпуска?")
    assert len(pipe.calls) == calls and "доступ" in text_of(out).lower()
    assert any("справку о доходах" in t.text for t in env.deps.repo.tickets(CO))
    page.goto(live.url + "/staff")
    assert page.locator("text=Доступ отключён").count() >= 1
    for width in (375, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(live.url + "/staff")
        assert no_h_scroll(page), width
