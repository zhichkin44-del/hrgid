from __future__ import annotations

import json
import shutil
from pathlib import Path

from hrgid.law.ingest import load_chunks

TOC = (
    '<li><a href="/">Раздел I. Общие положения</a></li>'
    '<li><a href="/">Глава 1. Основные начала</a></li>'
    '<li><a href="/">Статья 1. Цели и задачи</a></li>'
    '<li><a href="/">Статья 2. Основные принципы</a></li>'
)


def test_load_chunks_adds_structure_and_edition(tmp_path: Path, fixtures_dir: Path) -> None:
    for name in ("st_1.html", "st_2.html"):
        shutil.copy(fixtures_dir / name, tmp_path / name)
    (tmp_path / "toc.html").write_text(TOC, encoding="utf-8")
    (tmp_path / "SOURCE.json").write_text(json.dumps({"edition": "25.05.2026"}), encoding="utf-8")
    chunks, stats = load_chunks(tmp_path)
    assert stats.articles == 2 and stats.chunks == len(chunks) > 2
    assert {c.tenant_id for c in chunks} == {"law"}
    assert {c.edition for c in chunks} == {"25.05.2026"}
    assert all(c.chapter == "Глава 1. Основные начала" for c in chunks)
    assert all(c.section == "Раздел I. Общие положения" for c in chunks)
    keys = [(c.article, c.part, c.sub) for c in chunks]
    assert len(keys) == len(set(keys))
