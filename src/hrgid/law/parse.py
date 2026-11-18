"""Разбор страницы статьи ТК (consultant.ru) на статью → части → фрагменты."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

LAW_TENANT = "law"
_PART_START = re.compile(r"^(\d+(?:\.\d+)*)\.\s+(?=\S)")
_TITLE = re.compile(r"^(?:ТК РФ\s+)?Статья\s+(\d+(?:\.\d+)?)\.\s*(.*)$", re.S)
_NOTE_CLASSES = ("doc-edit", "doc-insert", "doc-comment", "document__edit", "document__insert")


@dataclass(frozen=True)
class Part:
    number: str | None
    text: str


@dataclass(frozen=True)
class Article:
    number: str
    title: str
    parts: list[Part]
    repealed: bool = False
    chapter: str = ""
    section: str = ""


@dataclass(frozen=True)
class Chunk:
    tenant_id: str
    article: str
    part: str | None
    sub: int
    title: str
    chapter: str
    section: str
    text: str
    edition: str

    @property
    def ref(self) -> str:
        r = f"ТК РФ, ст. {self.article}"
        return f"{r}, ч. {self.part}" if self.part else r

    @property
    def embed_text(self) -> str:
        return f"Статья {self.article}. {self.title}\n{self.text}"

    @property
    def content_hash(self) -> str:
        raw = f"{self.article}|{self.part}|{self.sub}|{self.text}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _in_note(tag: Tag) -> bool:
    for parent in tag.parents:
        classes = parent.get("class") if isinstance(parent, Tag) else None
        if classes and any(c in _NOTE_CLASSES for c in classes):
            return True
    return False


def parse_article_html(html: str) -> Article:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.document-page__content")
    if content is None:
        raise ValueError("нет блока содержимого статьи")
    h1 = content.find("h1")
    if h1 is None:
        raise ValueError("нет заголовка статьи")
    m = _TITLE.match(_clean(h1.get_text(" ")))
    if m is None:
        raise ValueError(f"не распознан заголовок: {h1.get_text()[:80]!r}")
    number, title = m.group(1), m.group(2).strip()

    paragraphs: list[str] = []
    for p in content.find_all("p"):
        if p.find_parent("h1") is not None or _in_note(p) or p.find("p") is not None:
            continue
        text = _clean(p.get_text(" "))
        text = re.sub(r"\s+([,.;:)])", r"\1", text)
        text = re.sub(r"([(])\s+", r"\1", text)
        if text:
            paragraphs.append(text)

    repealed = title.lower().startswith("утратил") or (
        len(paragraphs) == 1 and paragraphs[0].lower().startswith("утратил")
    )
    parts: list[Part] = []
    current_no: str | None = None
    buf: list[str] = []
    seen_numbered = False
    last_major = 0

    for para in paragraphs:
        pm = _PART_START.match(para)
        if pm:
            key = pm.group(1)
            major = int(key.split(".")[0])
            is_start = (not seen_numbered and key in ("1", "1.1")) or (
                seen_numbered and (major == last_major + 1 or (major == last_major and "." in key))
            )
            if is_start:
                if buf:
                    parts.append(Part(current_no, "\n".join(buf)))
                buf = []
                current_no = key
                seen_numbered = True
                last_major = major
                para = para[pm.end() :]
        buf.append(para)
    if buf:
        parts.append(Part(current_no, "\n".join(buf)))
    return Article(number=number, title=title, parts=parts, repealed=repealed)


def make_chunks(art: Article, edition: str, max_chars: int = 1200) -> list[Chunk]:
    if art.repealed:
        return []
    chunks: list[Chunk] = []
    for part in art.parts:
        pieces: list[str] = []
        cur = ""
        for para in part.text.split("\n"):
            if cur and len(cur) + len(para) + 1 > max_chars:
                pieces.append(cur)
                cur = para
            else:
                cur = f"{cur}\n{para}" if cur else para
        if cur:
            pieces.append(cur)
        for i, piece in enumerate(pieces):
            chunks.append(
                Chunk(
                    tenant_id=LAW_TENANT,
                    article=art.number,
                    part=part.number,
                    sub=i,
                    title=art.title,
                    chapter=art.chapter,
                    section=art.section,
                    text=piece,
                    edition=edition,
                )
            )
    return chunks
