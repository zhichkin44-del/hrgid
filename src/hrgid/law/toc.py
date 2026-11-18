"""Оглавление ТК РФ: статья → (раздел, глава) по порядку ссылок на странице оглавления."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

_HEADING = re.compile(r"^(Раздел|Подраздел|Глава)\s+[0-9IVXLC.]+[.\s]")
_ARTICLE = re.compile(r"^Статья\s+(\d+(?:\.\d+)?)\.")


def parse_toc(html: str) -> dict[str, tuple[str, str]]:
    section = ""
    chapter = ""
    result: dict[str, tuple[str, str]] = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a"):
        text = re.sub(r"\s+", " ", a.get_text(" ")).strip()
        m = _ARTICLE.match(text)
        if m:
            result.setdefault(m.group(1), (section, chapter))
        elif _HEADING.match(text):
            if text.startswith("Раздел"):
                section, chapter = text, ""
            elif text.startswith("Глава"):
                chapter = text
    return result
