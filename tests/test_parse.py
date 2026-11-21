from dataclasses import replace
from pathlib import Path

from hrgid.law.parse import Chunk, make_chunks, parse_article_html

FIX = Path(__file__).parent / "fixtures"

NUMBERED = """<html><body><div class="document-page__content">
<div class="document__style doc-style"><h1><p><a id="d1"></a>ТК РФ Статья 99. Тестовая статья</p></h1></div>
<p class="no-indent"></p>
<p><a id="d2"></a>1. Первая часть <a href="/x">со ссылкой</a>.</p>
<p>1) пункт первый;</p>
<p>2) пункт второй.</p>
<div class="document__edit doc-edit"><p class="no-indent">(в ред. закона от 01.01.2020 N 1-ФЗ)</p></div>
<p>2. Вторая часть.</p>
<div class="document__insert doc-insert"><p>(см. текст в предыдущей редакции)</p></div>
</div><div class="document-page__notes"></div></body></html>"""

REPEALED = """<html><body><div class="document-page__content">
<div class="document__style"><h1><p>ТК РФ Статья 5.1. Утратила силу</p></h1></div>
<p>Утратила силу. - Федеральный закон от 30.06.2006 N 90-ФЗ.</p></div></body></html>"""


def test_unnumbered_article() -> None:
    art = parse_article_html((FIX / "st_2.html").read_text(encoding="utf-8"))
    assert art.number == "2"
    assert art.title.startswith("Основные принципы правового регулирования")
    assert len(art.parts) == 1
    assert art.parts[0].number is None
    assert "свобода труда" in art.parts[0].text
    assert "в ред." not in art.parts[0].text
    assert "ТК РФ" not in art.title


def test_numbered_parts_and_notes_stripped() -> None:
    art = parse_article_html(NUMBERED)
    assert art.number == "99"
    assert [p.number for p in art.parts] == ["1", "2"]
    assert "Первая часть со ссылкой." in art.parts[0].text
    assert "2) пункт второй." in art.parts[0].text
    assert "в ред." not in art.parts[0].text
    assert "предыдущей редакции" not in art.parts[1].text


def test_repealed_flag() -> None:
    art = parse_article_html(REPEALED)
    assert art.number == "5.1"
    assert art.repealed is True


def test_chunks_have_metadata_and_stable_hash() -> None:
    art = replace(parse_article_html(NUMBERED), chapter="Глава 1", section="Раздел I")
    chunks = make_chunks(art, edition="25.05.2026", max_chars=1200)
    assert len(chunks) == 2
    c: Chunk = chunks[0]
    assert c.ref == "ТК РФ, ст. 99, ч. 1"
    assert c.tenant_id == "law"
    assert c.edition == "25.05.2026"
    assert c.chapter == "Глава 1"
    assert c.content_hash == make_chunks(art, edition="25.05.2026")[0].content_hash
    assert c.embed_text.startswith("Статья 99. Тестовая статья")


def test_long_part_is_split_by_paragraphs() -> None:
    para = "Слово " * 120  # about 720 characters
    html = NUMBERED.replace("1) пункт первый;", para).replace("2) пункт второй.", para)
    art = parse_article_html(html)
    chunks = make_chunks(art, edition="x", max_chars=1000)
    first_part = [c for c in chunks if c.part == "1"]
    assert len(first_part) >= 2
    assert [c.sub for c in first_part] == list(range(len(first_part)))


def test_repealed_article_gives_no_chunks() -> None:
    art = parse_article_html(REPEALED)
    assert make_chunks(art, edition="x") == []
