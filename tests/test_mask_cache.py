from __future__ import annotations

import pytest

from hrgid.flow.cache import MemoryCache, cache_key
from hrgid.flow.mask import Masker, MaskMap


def _names(text: str) -> list[tuple[int, int]]:
    out = []
    for name in ("Иванова Ивана", "Иванов Иван"):
        i = text.find(name)
        if i >= 0:
            out.append((i, i + len(name)))
    return out


def test_patterns_masked() -> None:
    m = Masker(name_finder=lambda t: [])
    text = (
        "Паспорт 45 12 345678, СНИЛС 112-233-445 95, ИНН 7707083893, "
        "тел +7 (916) 123-45-67, почта ivan@example.ru"
    )
    out, mp = m.mask(text)
    for raw in ("345678", "112-233-445", "7707083893", "123-45-67", "ivan@example.ru"):
        assert raw not in out
    assert len(mp.items) == 5
    back, unknown = mp.unmask(out)
    assert back == text and unknown == []


def test_name_any_case_same_label() -> None:
    m = Masker(name_finder=_names)
    out, mp = m.mask("Уволить Иванова Ивана? Иванов Иван просит отпуск")
    assert "Иванов" not in out
    assert out.count("[ФИО_") == 2 and len(mp.items) == 2  # different cases: different labels


def test_repeated_value_one_label() -> None:
    m = Masker(name_finder=lambda t: [])
    out, mp = m.mask("тел 89161234567 и снова 89161234567")
    assert out.count("[ТЕЛЕФОН_1]") == 2 and len(mp.items) == 1


def test_foreign_or_modified_label_not_revealed() -> None:
    m = Masker(name_finder=_names)
    _, mp = m.mask("Иванов Иван")
    other = MaskMap()
    other.label("ФИО", "Петров Пётр")
    text, unknown = mp.unmask("Ответ для [ФИО_1], копия [ФИО_2], [ПАСПОРТ_1]")
    assert text == "Ответ для Иванов Иван, копия [скрыто], [скрыто]"
    assert unknown == ["[ФИО_2]", "[ПАСПОРТ_1]"]
    # a label from another request is not revealed by a foreign table
    t2, u2 = MaskMap().unmask("[ФИО_1]")
    assert t2 == "[скрыто]" and u2 == ["[ФИО_1]"]


def test_plain_question_untouched() -> None:
    out, mp = Masker(name_finder=lambda t: []).mask("Сколько дней отпуска положено?")
    assert out == "Сколько дней отпуска положено?" and not mp.items


@pytest.mark.natasha
def test_natasha_finds_names_in_cases() -> None:
    out, _ = Masker().mask("Можно ли уволить Иванова Ивана Петровича по статье 81?")
    assert "Иванов" not in out and "[ФИО_1]" in out


def test_cache_key_isolation_and_normalisation() -> None:
    k = cache_key("c1", "g", "v1", "Сколько  дней ОТПУСКА?")
    assert k == cache_key("c1", "g", "v1", "сколько дней отпуска?")
    assert k != cache_key("c2", "g", "v1", "сколько дней отпуска?")
    assert k != cache_key("c1", "g2", "v1", "сколько дней отпуска?")
    assert k != cache_key("c1", "g", "v2", "сколько дней отпуска?")
    c = MemoryCache()
    c.put(k, 1)
    assert c.get(cache_key("c2", "g", "v1", "сколько дней отпуска?")) is None
    assert c.get(k) == 1
