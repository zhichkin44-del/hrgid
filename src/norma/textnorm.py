"""Нормализация текста для сравнения цитат с источником (п. 12.2 спецификации)."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = dict.fromkeys(map(ord, "«»„“”‟‹›‚‘’‛`´"), '"')
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−–"), "-")
_FOOTNOTE = re.compile(r"<\d+>|\[\d+\]|\(\d+\)(?=\s|$)|\*+")
_LINEBREAK_HYPHEN = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Привести текст к виду, пригодному для дословного сравнения."""
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("­", "")
    t = _LINEBREAK_HYPHEN.sub("", t)
    t = t.translate(_QUOTES).translate(_DASHES)
    t = _FOOTNOTE.sub("", t)
    t = t.lower().replace("ё", "е")
    return _SPACES.sub(" ", t).strip()
