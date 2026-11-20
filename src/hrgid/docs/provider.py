"""RulesProvider для бота (/rules): действующие версии документов компании."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from hrgid.bot.engine import RulesDoc
from hrgid.docs.service import DocService


class DocRulesProvider:
    def __init__(self, svc: DocService, today: Callable[[], date] = date.today) -> None:
        self.svc = svc
        self.today = today

    def _active(self, company_id: str) -> dict[str, RulesDoc]:
        out: dict[str, RulesDoc] = {}
        for d in self.svc.documents(company_id):
            v = self.svc.active_version(company_id, d["doc_id"], self.today())
            if v is None or v.effective_from is None or v.order_date is None:
                continue
            title = str(d["title"])
            out[title.lower()] = RulesDoc(
                title=title,
                version=str(v.number),
                order=f"№ {v.order_no} от {v.order_date:%d.%m.%Y}",
                since=v.effective_from,
                items=[f"{i.number} {i.text}" for i in v.items if not i.heading],
            )
        return out

    def topics(self, company_id: str) -> list[str]:
        return sorted(self._active(company_id))

    def topic(self, company_id: str, topic: str) -> RulesDoc | None:
        return self._active(company_id).get(topic.lower())
