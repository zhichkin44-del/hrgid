"""База ответов HR (SPEC-09 §8 П3, §5 п.2): сохранение ответа и поиск вместе с документами."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from hrgid.bot.models import KbEntry
from hrgid.bot.repo import Repo
from hrgid.flow.graph import Searcher
from hrgid.search import SearchHit
from hrgid.textnorm import normalize

_WORD = re.compile(r"[a-zа-я0-9]+")
SAME_QUESTION = 0.6  # доля общих основ слов: «тот же вопрос»
RELATED = 0.35


class KbError(ValueError):
    """Ошибка полей «Сохранить в базу»: field — какое поле подсветить."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def stems(text: str) -> set[str]:
    return {w[:5] for w in _WORD.findall(normalize(text)) if len(w) >= 3}


def similarity(a: str, b: str) -> float:
    sa, sb = stems(a), stems(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class KbService:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def add(
        self,
        company_id: str,
        *,
        question: str,
        answer: str,
        basis: str,
        author: str,
        now: datetime,
        audience: str = "all",
        source_ticket: str = "",
    ) -> KbEntry:
        if not question.strip():
            raise KbError("question", "Нужен вопрос")
        if not answer.strip():
            raise KbError("answer", "Нужен ответ")
        if not basis.strip():
            raise KbError("basis", "Укажите основание: статью ТК или пункт правил")
        entry = KbEntry(
            id=self.repo.next_kb_id(company_id),
            company_id=company_id,
            question=question.strip(),
            answer=answer.strip(),
            basis=basis.strip(),
            audience=audience,
            author=author,
            created_at=now,
            source_ticket=source_ticket,
        )
        self.repo.save_kb(entry)
        return entry

    def ranked(
        self, company_id: str, question: str, group: str = ""
    ) -> list[tuple[float, KbEntry]]:
        res = [
            (similarity(question, e.question), e)
            for e in self.repo.kb_entries(company_id)
            if e.audience in ("all", group) and not e.archived
        ]
        return sorted((r for r in res if r[0] > 0), key=lambda r: -r[0])

    def find_same(self, company_id: str, question: str, group: str = "") -> KbEntry | None:
        """Тот же вопрос уже решён HR (сценарий 2): ответ отдаётся из базы."""
        ranked = self.ranked(company_id, question, group)
        if not ranked or ranked[0][0] < SAME_QUESTION:
            return None
        hit = ranked[0][1].model_copy(update={"uses": ranked[0][1].uses + 1})
        self.repo.save_kb(hit)
        return hit


class KbSearcher:
    """Searcher потока этапа 2: документы + база ответов HR того же tenant (§5 п.2)."""

    def __init__(self, base: Searcher, kb: KbService) -> None:
        self.base = base
        self.kb = kb

    def search(self, question: str, tenants: Sequence[str]) -> list[SearchHit]:
        hits = list(self.base.search(question, tenants))
        extra: list[SearchHit] = []
        for company in tenants:
            for sim, e in self.kb.ranked(company, question)[:2]:
                if sim >= RELATED:
                    extra.append(
                        SearchHit(
                            id=-e.id,  # отрицательные id — записи базы ответов
                            tenant_id=company,
                            article="База ответов HR",
                            part="",
                            title=e.question,
                            ref=f"Ответ HR от {e.created_at:%d.%m.%Y} ({e.basis})",
                            text=e.answer,
                            edition="",
                            score=sim,
                        )
                    )
        return extra + hits
