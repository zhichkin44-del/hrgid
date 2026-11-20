"""У каждого раздела — пусто / загрузка / ошибка / нет прав (SPEC-09 §8)."""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import ADMIN, HR, make_env

# раздел -> (адрес, что сломать, текст пустого состояния на свежей компании)
SECTIONS: list[tuple[str, str, tuple[str, str], str]] = [
    ("today", "/", ("repo", "tickets"), "Начало работы"),
    ("queue", "/queue", ("repo", "tickets"), "В очереди пусто"),
    ("kb", "/kb", ("repo", "kb_entries"), "База ответов пока пуста"),
    ("docs", "/docs", ("docs", "documents"), "Документов пока нет"),
    ("check", "/check", ("docs", "documents"), "Пока нечего проверять"),
    ("staff", "/staff", ("repo", "employees"), "Сотрудников пока нет"),
    ("ann", "/announcements", ("repo", "announcements"), "Объявлений пока нет"),
    ("analytics", "/analytics", ("ops", "questions"), "Данных пока нет"),
    ("costs", "/costs", ("ops", "tech"), "Расход за месяц"),
    ("settings", "/settings", ("ops", "tech"), "Настройки"),
    ("journal", "/journal", ("journal", "entries"), "Журнал пуст"),
]


@pytest.mark.parametrize(("name", "url", "_", "empty"), SECTIONS)
def test_empty_state(name: str, url: str, _: tuple[str, str], empty: str) -> None:
    env = make_env()
    r = env.login(ADMIN).get(url)
    assert r.status_code == 200 and empty in r.text, name


@pytest.mark.parametrize(("name", "url", "broken", "_"), SECTIONS)
def test_error_state_has_retry_and_no_details(
    name: str, url: str, broken: tuple[str, str], _: str
) -> None:
    env = make_env()
    c = env.login(ADMIN)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("Секрет Иван Иванов 123-456-789 01")

    setattr(getattr(env.deps, broken[0]), broken[1], boom)
    r = c.get(url)
    assert r.status_code == 500, name
    assert "Не удалось загрузить раздел" in r.text and "Повторить" in r.text
    assert f'href="{url.split("?")[0]}"' in r.text  # «Повторить» ведёт на тот же раздел
    assert "Секрет" not in r.text and "123-456-789" not in r.text and "Traceback" not in r.text
    assert "Вернуться на «Сегодня»" in r.text  # не тупик


def test_loading_state_page_has_skeleton_and_exit() -> None:
    r = make_env().login(HR).get("/loading")
    assert 'class="skel"' in r.text and "дольше 10 секунд" in r.text and 'href="/"' in r.text


def test_no_rights_state_for_hr_settings_names_who_can() -> None:
    env = make_env()
    r = env.login(HR).get("/settings")
    assert r.status_code == 403 and "только для администратора" in r.text
    assert 'href="/costs/ask"' in r.text and "Вернуться на «Сегодня»" in r.text


def test_not_found_state_is_not_a_dead_end() -> None:
    env = make_env()
    r = env.login(HR).get("/kb/999/edit")
    assert r.status_code == 404 and "Вернуться на «Сегодня»" in r.text


def test_busy_indicator_and_mobile_menu_in_every_page() -> None:
    env = make_env()
    r = env.login(HR).get("/queue")
    assert 'id="busy"' in r.text and "menu-btn" in r.text and 'hx-boost="true"' in r.text


def test_window_query_param_w_opens_window() -> None:
    env = make_env()
    c = env.login(HR)
    assert "Загрузить документ" in c.get("/docs?w=upload").text.split("П4.1")[1]
    assert "П6.2" in c.get("/staff?w=import1").text
    assert "П10.1" in c.get("/journal?w=filters").text
