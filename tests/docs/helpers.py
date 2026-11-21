"""Common helpers of the stage 5 tests: the source of norms by keywords and the Fake verification model."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

from hrgid.docs.tkcheck import Norm
from hrgid.llm.base import LLMRequest, LLMResponse
from hrgid.llm.fake import FakeLLM

FIX = Path(__file__).resolve().parents[1] / "fixtures"
DRAFT = Path(__file__).resolve().parents[2] / "data" / "companies" / "studio" / "rules-draft.md"
VIOLATIONS = FIX / "rules_studio_violations.md"

COMPANY = "studio"


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z0-9]+", text.lower()) if len(w) > 3}


class KeywordNormSource:
    """A replacement for hybrid search (it needs PostgreSQL): intersection of word stems."""

    def __init__(self) -> None:
        raw = json.loads((FIX / "tk_norms.json").read_text(encoding="utf-8"))
        self.norms = [Norm(n["ref"], n["article"], n["text"]) for n in raw["norms"]]

    def find(self, text: str, k: int = 4) -> list[Norm]:
        q = _stems(text)
        scored = sorted(self.norms, key=lambda n: -len(q & _stems(n.text)))
        return [n for n in scored[:k] if q & _stems(n.text)]


_MONTHS = {"три": 3, "3": 3, "шесть": 6, "6": 6, "два": 2, "2": 2}


def _json(verdict: str, why: str, article: str = "", sug: str = "") -> str:
    return json.dumps(
        {"verdict": verdict, "explanation": why, "article": article, "suggestion": sug},
        ensure_ascii=False,
    )


def fake_judge(req: LLMRequest) -> LLMResponse | str:
    """A model simulation with recordings: simple rules over the item text (mechanics, not quality)."""
    user = req.messages[0].content
    body = user.split("ПУНКТ ПРАВИЛ", 1)[1]
    text = body.split("<<<", 1)[1].split(">>>", 1)[0].strip().lower()
    m = re.search(r"испытани\w*\s+продолжительностью\s+(?:до\s+)?(\w+)\s+месяц", text)
    if m and "руководител" not in text:
        n = _MONTHS.get(m.group(1), 0)
        if n > 3:
            return _json(
                "risk", f"Испытание {n} мес. рядовому работнику дольше предела в три месяца.",
                "ст. 70", "Работнику устанавливается испытание продолжительностью до трёх месяцев.",
            )  # fmt: skip
    if "ежегодн" in text and "отпуск" in text:
        d = re.search(r"(\d+) календарн", text)
        if d and int(d.group(1)) < 28:
            return _json(
                "risk", f"{d.group(1)} дней меньше минимальных 28 календарных дней.", "ст. 115",
                "Ежегодный основной оплачиваемый отпуск предоставляется продолжительностью "
                "не менее 28 календарных дней.",
            )  # fmt: skip
        if d and int(d.group(1)) > 28:
            return _json("better", "Отпуск длиннее минимального (28 дней).", "ст. 115")
    monthly = "раз в месяц" in text and "два раза" not in text
    if "заработная плата выплачивается" in text and monthly:
        return _json(
            "risk", "Выплата реже чем каждые полмесяца.", "ст. 136",
            "Заработная плата выплачивается два раза в месяц: 25 числа и 10 числа "
            "следующего месяца.",
        )  # fmt: skip
    return _json("ok", "Пункт не противоречит найденным нормам ТК.", "")


def make_fake() -> FakeLLM:
    return FakeLLM(fake_judge)


def write_docx(md: Path, path: Path) -> bytes:
    from docx import Document

    doc = Document()
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith(">"):
            doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    path.write_bytes(data)
    return data
