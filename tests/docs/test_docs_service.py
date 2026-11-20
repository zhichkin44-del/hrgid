from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path

import pytest

from hrgid.bot.store import MemoryDocStore
from hrgid.docs.parse import DocError, parse_document, split_items
from hrgid.docs.provider import DocRulesProvider
from hrgid.docs.service import DocService

from .helpers import COMPANY, DRAFT, write_docx

TODAY = date(2026, 12, 1)


def _svc(affected: int = 0) -> DocService:
    return DocService(
        MemoryDocStore(), clock=lambda: datetime(2026, 11, 20, 12, 0),
        affected_answers=lambda c, d: affected,
    )  # fmt: skip


def _docx(tmp: Path, text: str) -> bytes:
    md = tmp / "r.md"
    md.write_text(text, encoding="utf-8")
    return write_docx(md, tmp / "r.docx")


def test_split_items_numbers_headings_and_continuation() -> None:
    items = split_items("1. Общие положения\n1.1. Первый пункт\nпродолжение строки\n1.2) Второй.")
    assert [i.number for i in items] == ["1", "1.1", "1.2"]
    assert items[0].heading and not items[1].heading
    assert items[1].text == "Первый пункт продолжение строки"


def test_docx_roundtrip_draft(tmp_path: Path) -> None:
    items = parse_document("r.docx", _docx(tmp_path, DRAFT.read_text(encoding="utf-8")))
    nums = [i.number for i in items]
    assert "5.1" in nums and "6.1" in nums and nums[0] == "1"
    assert sum(1 for i in items if not i.heading) > 25


def test_pdf_roundtrip(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font = Path("C:/Windows/Fonts/arial.ttf")
    if not font.exists():
        pytest.skip("нет шрифта с кириллицей")
    pdfmetrics.registerFont(TTFont("Cyr", str(font)))
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFont("Cyr", 11)
    c.drawString(50, 780, "5.1. Испытание до трёх месяцев.")
    c.drawString(50, 760, "6.1. Отпуск 31 календарный день.")
    c.save()
    items = parse_document("r.pdf", buf.getvalue())
    assert [i.number for i in items] == ["5.1", "6.1"]


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("r.txt", b"x", "format"),
        ("r.docx", b"not a zip", "corrupt"),
        ("r.pdf", b"%PDF-broken", "corrupt"),
        ("r.docx", b"x" * (20 * 1024 * 1024 + 1), "too_big"),
    ],
)
def test_upload_errors(name: str, data: bytes, code: str) -> None:
    with pytest.raises(DocError) as e:
        _svc().upload(COMPANY, name, data, "admin")
    assert e.value.code == code


def test_encrypted_pdf_rejected() -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(DocError) as e:
        parse_document("r.pdf", buf.getvalue())
    assert e.value.code == "password"


def test_version_not_applied_without_order(tmp_path: Path) -> None:
    svc = _svc()
    v = svc.upload(COMPANY, "r.docx", _docx(tmp_path, "1.1. Один пункт"), "admin", title="Правила")
    assert svc.active_version(COMPANY, v.doc_id, TODAY) is None
    assert DocRulesProvider(svc, lambda: TODAY).topics(COMPANY) == []


def test_order_validation(tmp_path: Path) -> None:
    svc = _svc()
    v = svc.upload(COMPANY, "r.docx", _docx(tmp_path, "1.1. Один пункт"), "admin")
    with pytest.raises(DocError) as e:
        svc.set_order(COMPANY, v.doc_id, 1, " ", date(2026, 11, 1), date(2026, 11, 10), "a")
    assert e.value.code == "order_no"
    with pytest.raises(DocError) as e2:
        svc.set_order(COMPANY, v.doc_id, 1, "5", date(2026, 11, 10), date(2026, 11, 1), "a")
    assert e2.value.code == "date_before_order"


def test_future_effective_date_not_active_yet(tmp_path: Path) -> None:
    svc = _svc()
    v = svc.upload(COMPANY, "r.docx", _docx(tmp_path, "1.1. Один пункт"), "admin")
    svc.set_order(COMPANY, v.doc_id, 1, "7", date(2026, 11, 1), date(2027, 1, 1), "a")
    assert svc.active_version(COMPANY, v.doc_id, TODAY) is None
    assert svc.active_version(COMPANY, v.doc_id, date(2027, 1, 1)) is not None


def _two_versions(svc: DocService, tmp: Path) -> str:
    v1 = svc.upload(COMPANY, "r.docx", _docx(tmp, "1.1. Старый текст\n1.2. Общий"), "admin", "Пр")
    svc.set_order(COMPANY, v1.doc_id, 1, "1", date(2026, 1, 1), date(2026, 1, 10), "admin")
    v2 = svc.upload(
        COMPANY, "r2.docx", _docx(tmp, "1.1. Новый текст\n1.2. Общий\n1.3. Добавлен"), "admin",
        doc_id=v1.doc_id,
    )  # fmt: skip
    assert v2.number == 2 and v2.parent == 1
    svc.set_order(COMPANY, v1.doc_id, 2, "2", date(2026, 6, 1), date(2026, 6, 10), "admin")
    return v1.doc_id


def test_compare_versions(tmp_path: Path) -> None:
    svc = _svc()
    doc = _two_versions(svc, tmp_path)
    rows = {r.number: r.status for r in svc.compare(COMPANY, doc, 1, 2)}
    assert rows == {"1.1": "changed", "1.2": "same", "1.3": "added"}
    back = {r.number: r.status for r in svc.compare(COMPANY, doc, 2, 1)}
    assert back["1.3"] == "removed"


def test_rollback_needs_reason_and_changes_active(tmp_path: Path) -> None:
    svc = _svc(affected=4)
    doc = _two_versions(svc, tmp_path)
    assert svc.active_version(COMPANY, doc, TODAY).number == 2  # type: ignore[union-attr]
    with pytest.raises(DocError):
        svc.rollback(COMPANY, doc, 1, " ", "admin", TODAY)
    res = svc.rollback(COMPANY, doc, 1, "ошибка в версии 2", "admin", TODAY)
    assert res.affected_answers == 4
    assert svc.active_version(COMPANY, doc, TODAY).number == 1  # type: ignore[union-attr]
    log = svc.audit_log(COMPANY)
    assert log[-1]["action"] == "doc.rollback" and log[-1]["details"]["reason"]


def test_rollback_to_unapproved_refused(tmp_path: Path) -> None:
    svc = _svc()
    doc = _two_versions(svc, tmp_path)
    v3 = svc.upload(COMPANY, "r3.docx", _docx(tmp_path, "1.1. Черновик"), "admin", doc_id=doc)
    with pytest.raises(DocError) as e:
        svc.rollback(COMPANY, doc, v3.number, "причина", "admin", TODAY)
    assert e.value.code == "not_applicable"


def test_cannot_delete_active_version_but_can_old(tmp_path: Path) -> None:
    svc = _svc()
    doc = _two_versions(svc, tmp_path)
    with pytest.raises(DocError) as e:
        svc.delete_version(COMPANY, doc, 2, "admin", TODAY)
    assert e.value.code == "active"
    svc.delete_version(COMPANY, doc, 1, "admin", TODAY)
    assert [v.number for v in svc.versions(COMPANY, doc)] == [2]


def test_tenant_isolation(tmp_path: Path) -> None:
    svc = _svc()
    doc = _two_versions(svc, tmp_path)
    assert svc.documents("other") == []
    with pytest.raises(DocError):
        svc.version("other", doc, 1)
    with pytest.raises(DocError):
        svc.upload("other", "x.docx", _docx(tmp_path, "1.1. a"), "u", doc_id=doc)
    assert DocRulesProvider(svc, lambda: TODAY).topics("other") == []


def test_provider_gives_active_rules(tmp_path: Path) -> None:
    svc = _svc()
    doc = _two_versions(svc, tmp_path)
    p = DocRulesProvider(svc, lambda: TODAY)
    assert p.topics(COMPANY) == ["пр"]
    d = p.topic(COMPANY, "Пр")
    assert d is not None and d.version == "2" and d.order == "№ 2 от 01.06.2026"
    assert d.items[0] == "1.1 Новый текст" and d.since == date(2026, 6, 10)
    svc.rollback(COMPANY, doc, 1, "откат", "admin", TODAY)
    again = p.topic(COMPANY, "пр")
    assert again is not None and again.items[0] == "1.1 Старый текст"
