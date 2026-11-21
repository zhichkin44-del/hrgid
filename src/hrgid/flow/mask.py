"""Masking of personal data before it is sent to the model (§9).

natasha finds full names in any grammatical case; templates cover passport, SNILS, TIN, phone and
email.
Labels [ФИО_1], [ПАСПОРТ_1] ... ; the mapping table lives only in the memory of the request.
A label that is not in the table (foreign, or changed by the model) is not revealed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

NameFinder = Callable[[str], list[tuple[int, int]]]

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("СНИЛС", re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]\d{2}\b")),
    (
        "ПАСПОРТ",
        re.compile(r"\b\d{2}\s?\d{2}\s?\d{6}\b|(?:паспорт\w*\s*)?серия\s*\d{4}\s*№?\s*\d{6}", re.I),
    ),
    ("ИНН", re.compile(r"(?:ИНН\s*:?\s*)\d{10,12}\b|\b\d{12}\b", re.I)),
    (
        "ТЕЛЕФОН",
        re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"),
    ),
)
_LABEL = re.compile(r"\[([A-ZА-ЯЁ]+)_(\d+)\]")


@dataclass
class MaskMap:
    """Table "label -> original text". Not serialized and not logged."""

    items: dict[str, str] = field(default_factory=dict)
    _by_value: dict[tuple[str, str], str] = field(default_factory=dict)

    def label(self, kind: str, value: str) -> str:
        key = (kind, value.lower())
        if key in self._by_value:
            return self._by_value[key]
        n = sum(1 for k in self.items if k.startswith(f"[{kind}_")) + 1
        lab = f"[{kind}_{n}]"
        self.items[lab] = value
        self._by_value[key] = lab
        return lab

    def unmask(self, text: str) -> tuple[str, list[str]]:
        """Returns the original values. Unknown labels are replaced with "[скрыто]"."""
        unknown: list[str] = []

        def sub(m: re.Match[str]) -> str:
            lab = m.group(0)
            if lab in self.items:
                return self.items[lab]
            unknown.append(lab)
            return "[скрыто]"

        return _LABEL.sub(sub, text), unknown


_natasha_finder: NameFinder | None = None


def natasha_names(text: str) -> list[tuple[int, int]]:
    """Full-name spans (PER) through natasha; models are loaded once."""
    global _natasha_finder
    if _natasha_finder is None:
        from natasha import (
            Doc,
            NewsEmbedding,
            NewsNERTagger,
            Segmenter,
        )

        segmenter = Segmenter()
        tagger = NewsNERTagger(NewsEmbedding())

        def find(t: str) -> list[tuple[int, int]]:
            doc = Doc(t)
            doc.segment(segmenter)
            doc.tag_ner(tagger)
            return [(s.start, s.stop) for s in doc.spans if s.type == "PER"]

        _natasha_finder = find
    return _natasha_finder(text)


class Masker:
    def __init__(self, name_finder: NameFinder | None = None) -> None:
        self._names = name_finder or natasha_names

    def mask(self, text: str, mapping: MaskMap | None = None) -> tuple[str, MaskMap]:
        mp = mapping or MaskMap()
        spans: list[tuple[int, int, str]] = []
        for kind, pat in _PATTERNS:
            spans += [(m.start(), m.end(), kind) for m in pat.finditer(text)]
        spans += [(a, b, "ФИО") for a, b in self._names(text)]
        # on overlap, the earlier and longer match wins
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        chosen: list[tuple[int, int, str]] = []
        end = -1
        for a, b, kind in spans:
            if a >= end:
                chosen.append((a, b, kind))
                end = b
        out: list[str] = []
        pos = 0
        for a, b, kind in chosen:
            out.append(text[pos:a])
            out.append(mp.label(kind, text[a:b]))
            pos = b
        out.append(text[pos:])
        return "".join(out), mp
