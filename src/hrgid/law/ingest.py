"""Загрузка ТК РФ из data/raw: разбор всех статей в фрагменты с главой и редакцией."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from hrgid.law.parse import Chunk, make_chunks, parse_article_html
from hrgid.law.toc import parse_toc


@dataclass(frozen=True)
class IngestStats:
    articles: int
    repealed: int
    chunks: int
    failed: list[str]


def load_chunks(raw_dir: Path) -> tuple[list[Chunk], IngestStats]:
    source = json.loads((raw_dir / "SOURCE.json").read_text(encoding="utf-8"))
    edition = str(source["edition"])
    toc_path = raw_dir / "toc.html"
    structure = parse_toc(toc_path.read_text(encoding="utf-8")) if toc_path.exists() else {}
    chunks: list[Chunk] = []
    articles = repealed = 0
    failed: list[str] = []
    for path in sorted(raw_dir.glob("st_*.html")):
        try:
            art = parse_article_html(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            failed.append(f"{path.name}: {exc}")
            continue
        articles += 1
        if art.repealed:
            repealed += 1
        section, chapter = structure.get(art.number, ("", ""))
        art = dataclasses.replace(art, section=section, chapter=chapter)
        chunks.extend(make_chunks(art, edition))
    return chunks, IngestStats(articles, repealed, len(chunks), failed)
