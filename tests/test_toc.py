from __future__ import annotations

from hrgid.law.toc import parse_toc

HTML = """
<ul><li><a href="/x/">Раздел I. Общие положения</a></li><ul>
<li><a href="/x/">Глава 1. Основные начала</a></li><ul>
<li><a href="/x/">Статья 1. Цели</a></li><li><a href="/x/">Статья 2. Принципы</a></li></ul>
<li><a href="/x/">Глава 2. Трудовые отношения</a></li><ul>
<li><a href="/x/">Статья 15. Трудовые отношения</a></li><li><a href="/x/">Статья 15.1. Новая</a></li></ul>
</ul><li><a href="/x/">Раздел II. Социальное партнерство</a></li><ul>
<li><a href="/x/">Подраздел 1. Что-то</a></li>
<li><a href="/x/">Глава 3. Общие</a></li><ul><li><a href="/x/">Статья 23. Общие</a></li></ul></ul></ul>
"""


def test_toc_assigns_section_and_chapter() -> None:
    m = parse_toc(HTML)
    assert m["1"] == ("Раздел I. Общие положения", "Глава 1. Основные начала")
    assert m["15.1"] == ("Раздел I. Общие положения", "Глава 2. Трудовые отношения")
    assert m["23"] == ("Раздел II. Социальное партнерство", "Глава 3. Общие")
    assert len(m) == 5
