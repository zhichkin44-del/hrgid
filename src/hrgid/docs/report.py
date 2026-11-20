"""Отчёт о проверке на ТК: HTML и PDF (reportlab). Если PDF собрать нельзя — HTML и пометка."""

from __future__ import annotations

import html
from pathlib import Path

from hrgid.docs.tkcheck import LABELS, CheckReport

_FONTS = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def to_html(rep: CheckReport, title: str, version_note: str = "") -> str:
    rows = []
    for i in rep.items:
        acc = ""
        if i.accepted:
            acc = (
                f"<p><b>Принято с риском</b>: {html.escape(i.accepted['comment'])} "
                f"({html.escape(i.accepted['author'])}, {html.escape(i.accepted['at'])})</p>"
            )
        sug = f"<p>Предложено: {html.escape(i.suggestion)}</p>" if i.suggestion else ""
        art = f" — {html.escape(i.article)}" if i.article else ""
        rows.append(
            f"<section class='{i.verdict}'><h3>Пункт {html.escape(i.number)}: "
            f"{html.escape(i.label)}{art}</h3><p>{html.escape(i.text)}</p>"
            f"<p>{html.escape(i.explanation)}</p>{sug}{acc}</section>"
        )
    head = (
        f"<h1>Проверка на ТК РФ: {html.escape(title)}</h1><p>Версия {rep.version}. "
        f"{html.escape(version_note)} Соответствует: {rep.count('ok')}, лучше закона: "
        f"{rep.count('better')}, риск: {rep.count('risk')}, на проверку человеком: "
        f"{rep.count('unclear')}.</p>"
    )
    return (
        "<!doctype html><html lang='ru'><meta charset='utf-8'><title>Проверка на ТК</title>"
        f"<body>{head}{''.join(rows)}</body></html>"
    )


def to_pdf(rep: CheckReport, title: str, path: Path, version_note: str = "") -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    font = next((f for f in _FONTS if f.exists()), None)
    if font is None:
        raise OSError("нет шрифта с кириллицей для PDF")
    pdfmetrics.registerFont(TTFont("Cyr", str(font)))
    base = getSampleStyleSheet()["BodyText"]
    st = ParagraphStyle("c", parent=base, fontName="Cyr", fontSize=10, leading=13)
    h = ParagraphStyle("h", parent=st, fontSize=13, leading=16, spaceBefore=8)
    esc = html.escape
    story: list[Paragraph] = [
        Paragraph(f"Проверка на ТК РФ: {esc(title)}", ParagraphStyle("t", parent=h, fontSize=16)),
        Paragraph(
            f"Версия {rep.version}. {esc(version_note)} Соответствует: {rep.count('ok')}, "
            f"лучше закона: {rep.count('better')}, риск: {rep.count('risk')}, "
            f"на проверку человеком: {rep.count('unclear')}.",
            st,
        ),
    ]
    for i in rep.items:
        art = f" ({esc(i.article)})" if i.article else ""
        story.append(Paragraph(f"Пункт {esc(i.number)}: {esc(LABELS[i.verdict])}{art}", h))
        story.append(Paragraph(esc(i.text), st))
        story.append(Paragraph(esc(i.explanation), st))
        if i.suggestion:
            story.append(Paragraph(f"Предложено: {esc(i.suggestion)}", st))
        if i.accepted:
            story.append(
                Paragraph(
                    f"Принято с риском: {esc(i.accepted['comment'])} "
                    f"({esc(i.accepted['author'])}, {esc(i.accepted['at'])})",
                    st,
                )
            )
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


def export_report(
    rep: CheckReport, title: str, out_dir: Path, version_note: str = ""
) -> tuple[Path, str]:
    """-> (файл, формат). PDF; при невозможности — HTML (формат «html», в панели пометка)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"tk-{rep.doc_id}-v{rep.version}"
    try:
        p = out_dir / f"{stem}.pdf"
        to_pdf(rep, title, p, version_note)
        return p, "pdf"
    except (ImportError, OSError):
        p = out_dir / f"{stem}.html"
        p.write_text(to_html(rep, title, version_note), encoding="utf-8")
        return p, "html"
