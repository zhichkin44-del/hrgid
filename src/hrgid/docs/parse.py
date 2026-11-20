"""Чтение PDF/DOCX и разбор текста на пронумерованные пункты."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

MAX_BYTES = 20 * 1024 * 1024


class DocError(Exception):
    """Ошибка работы с документом; code — машинный признак, str — понятный HR текст."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Item:
    number: str
    text: str
    heading: bool = False

    def as_dict(self) -> dict[str, object]:
        return {"number": self.number, "text": self.text, "heading": self.heading}


_ITEM = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(\S.*)$")


def split_items(text: str) -> list[Item]:
    """Пункты вида «5.5 Текст…». Строки без номера дописываются к предыдущему пункту.

    Заголовок раздела — «1. Общие положения»: номер без точки внутри, короткий, без точки в конце.
    """
    raw: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _ITEM.match(line)
        if m:
            raw.append([m.group(1), m.group(2).strip()])
        elif raw:
            raw[-1][1] += " " + line
    items: list[Item] = []
    for number, body in raw:
        heading = "." not in number and len(body) <= 60 and not body.endswith((".", ";", ":"))
        items.append(Item(number, body, heading))
    return items


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocError("password", "Файл защищён паролем. Снимите защиту и загрузите снова.")
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except DocError:
        raise
    except (PyPdfError, ValueError, KeyError, OSError) as e:
        raise DocError("corrupt", "Файл повреждён или не читается как PDF.") from e


def _docx_text(data: bytes) -> str:
    import zipfile

    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise DocError("corrupt", "Файл повреждён или защищён паролем, DOCX не открывается.") from e
    return "\n".join(p.text for p in doc.paragraphs)


def read_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise DocError("too_big", "Файл больше 20 МБ.")
    name = filename.lower()
    if name.endswith(".pdf"):
        return _pdf_text(data)
    if name.endswith(".docx"):
        return _docx_text(data)
    raise DocError("format", "Нужен файл PDF или DOCX.")


def parse_document(filename: str, data: bytes) -> list[Item]:
    items = split_items(read_text(filename, data))
    if not items:
        raise DocError("no_items", "В файле не найдено пронумерованных пунктов (1.1, 5.5 …).")
    return items
