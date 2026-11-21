"""Text normalization for comparing quotes with the source (spec item 12.2)."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = dict.fromkeys(map(ord, "«»„“”‟‹›‚‘’‛`´"), '"')
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−–"), "-")
_FOOTNOTE = re.compile(r"<\d+>|\[\d+\]|\(\d+\)(?=\s|$)|\*+")
_LINEBREAK_HYPHEN = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Brings the text to a form suitable for verbatim comparison."""
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("­", "")
    t = _LINEBREAK_HYPHEN.sub("", t)
    t = t.translate(_QUOTES).translate(_DASHES)
    t = _FOOTNOTE.sub("", t)
    t = t.lower().replace("ё", "е")
    return _SPACES.sub(" ", t).strip()
