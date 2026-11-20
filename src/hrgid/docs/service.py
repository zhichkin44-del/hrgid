"""Документы и версии компании. Всё в границах company_id (tenant_id) через DocStore."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from hrgid.bot.store import DocStore
from hrgid.docs.parse import DocError, Item, parse_document

K_DOC, K_VER, K_AUDIT = "doc", "docver", "docaudit"


@dataclass(frozen=True)
class Version:
    doc_id: str
    number: int
    items: tuple[Item, ...]
    filename: str
    created_at: str
    author: str
    parent: int | None
    order_no: str
    order_date: date | None
    effective_from: date | None

    def applicable(self, today: date) -> bool:
        """Версия применяется только с номером приказа, датой приказа и датой начала действия."""
        return bool(
            self.order_no.strip()
            and self.order_date
            and self.effective_from
            and self.effective_from >= self.order_date
            and self.effective_from <= today
        )

    def item(self, number: str) -> Item | None:
        return next((i for i in self.items if i.number == number), None)

    @property
    def checkable(self) -> list[Item]:
        return [i for i in self.items if not i.heading]


@dataclass(frozen=True)
class DiffRow:
    number: str
    status: str  # same | changed | added | removed
    old: str
    new: str


@dataclass(frozen=True)
class RollbackResult:
    doc_id: str
    to_version: int
    affected_answers: int


def _d(v: date | None) -> str:
    return v.isoformat() if v else ""


def _pd(s: str) -> date | None:
    return date.fromisoformat(s) if s else None


def _ver_to_dict(v: Version) -> dict[str, Any]:
    return {
        "doc_id": v.doc_id,
        "number": v.number,
        "items": [i.as_dict() for i in v.items],
        "filename": v.filename,
        "created_at": v.created_at,
        "author": v.author,
        "parent": v.parent,
        "order_no": v.order_no,
        "order_date": _d(v.order_date),
        "effective_from": _d(v.effective_from),
    }


def _ver_from_dict(d: dict[str, Any]) -> Version:
    return Version(
        doc_id=d["doc_id"],
        number=int(d["number"]),
        items=tuple(Item(i["number"], i["text"], bool(i["heading"])) for i in d["items"]),
        filename=d["filename"],
        created_at=d["created_at"],
        author=d["author"],
        parent=d["parent"],
        order_no=d["order_no"],
        order_date=_pd(d["order_date"]),
        effective_from=_pd(d["effective_from"]),
    )


def _natural(num: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", num))


def diff_items(old: tuple[Item, ...], new: tuple[Item, ...]) -> list[DiffRow]:
    a = {i.number: i.text for i in old}
    b = {i.number: i.text for i in new}
    rows: list[DiffRow] = []
    for num in sorted(a.keys() | b.keys(), key=_natural):
        if num in a and num in b:
            rows.append(DiffRow(num, "same" if a[num] == b[num] else "changed", a[num], b[num]))
        elif num in b:
            rows.append(DiffRow(num, "added", "", b[num]))
        else:
            rows.append(DiffRow(num, "removed", a[num], ""))
    return rows


class DocService:
    def __init__(
        self,
        store: DocStore,
        clock: Callable[[], datetime] = datetime.now,
        affected_answers: Callable[[str, str], int] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self._affected = affected_answers

    def now_iso(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    # --- журнал ---
    def audit(
        self, company: str, author: str, action: str, doc_id: str = "", **details: Any
    ) -> None:
        n = self.store.next_seq(company, "audit")
        self.store.put(
            K_AUDIT,
            company,
            f"{n:08d}",
            {
                "at": self.now_iso(),
                "author": author,
                "action": action,
                "doc_id": doc_id,
                "details": details,
            },
        )

    def audit_log(self, company: str) -> list[dict[str, Any]]:
        return self.store.all(K_AUDIT, company)

    # --- документы и версии ---
    def _put_ver(self, company: str, v: Version) -> None:
        self.store.put(K_VER, company, f"{v.doc_id}:{v.number}", _ver_to_dict(v))

    def versions(self, company: str, doc_id: str) -> list[Version]:
        out = [_ver_from_dict(d) for d in self.store.all(K_VER, company) if d["doc_id"] == doc_id]
        return sorted(out, key=lambda v: v.number)

    def version(self, company: str, doc_id: str, number: int) -> Version:
        d = self.store.get(K_VER, company, f"{doc_id}:{number}")
        if d is None:
            raise DocError("no_version", f"Версии {number} нет.")
        return _ver_from_dict(d)

    def documents(self, company: str) -> list[dict[str, Any]]:
        return self.store.all(K_DOC, company)

    def _doc(self, company: str, doc_id: str) -> dict[str, Any]:
        d = self.store.get(K_DOC, company, doc_id)
        if d is None:
            raise DocError("no_doc", "Документ не найден.")
        return d

    def _new_version(
        self,
        company: str,
        doc_id: str,
        items: tuple[Item, ...],
        filename: str,
        author: str,
        parent: int | None,
    ) -> Version:
        n = self.store.next_seq(company, f"ver:{doc_id}")
        v = Version(doc_id, n, items, filename, self.now_iso(), author, parent, "", None, None)
        self._put_ver(company, v)
        return v

    def upload(
        self,
        company: str,
        filename: str,
        data: bytes,
        author: str,
        title: str = "",
        doc_id: str | None = None,
    ) -> Version:
        """Загрузить файл: новый документ или новая версия существующего.
        Версия — черновик, пока не указаны приказ и дата начала."""
        items = tuple(parse_document(filename, data))
        parent: int | None = None
        if doc_id is None:
            doc_id = f"d{self.store.next_seq(company, 'doc')}"
            self.store.put(
                K_DOC,
                company,
                doc_id,
                {
                    "doc_id": doc_id,
                    "title": title or filename.rsplit(".", 1)[0],
                    "override": None,
                    "created_at": self.now_iso(),
                },
            )
        else:
            self._doc(company, doc_id)
            existing = self.versions(company, doc_id)
            parent = existing[-1].number if existing else None
        v = self._new_version(company, doc_id, items, filename, author, parent)
        self.audit(company, author, "doc.upload", doc_id, version=v.number, items=len(items))
        return v

    def set_order(
        self,
        company: str,
        doc_id: str,
        number: int,
        order_no: str,
        order_date: date,
        effective_from: date,
        author: str,
    ) -> Version:
        if not order_no.strip():
            raise DocError("order_no", "Укажите номер приказа.")
        if effective_from < order_date:
            raise DocError("date_before_order", "Дата начала не может быть раньше даты приказа.")
        o = self.version(company, doc_id, number)
        v = Version(
            o.doc_id, o.number, o.items, o.filename, o.created_at, o.author, o.parent,
            order_no.strip(), order_date, effective_from,
        )  # fmt: skip
        self._put_ver(company, v)
        doc = self._doc(company, doc_id)
        doc["override"] = None  # новая утверждённая версия отменяет прежний откат
        self.store.put(K_DOC, company, doc_id, doc)
        self.audit(
            company,
            author,
            "doc.order",
            doc_id,
            version=number,
            order_no=order_no.strip(),
            order_date=order_date.isoformat(),
            effective_from=effective_from.isoformat(),
        )
        return v

    def active_version(self, company: str, doc_id: str, today: date) -> Version | None:
        doc = self._doc(company, doc_id)
        if doc.get("override"):
            v = self.version(company, doc_id, int(doc["override"]))
            if v.applicable(today):
                return v
        ok = [v for v in self.versions(company, doc_id) if v.applicable(today)]
        if not ok:
            return None
        return max(ok, key=lambda v: (v.effective_from or date.min, v.number))

    def compare(self, company: str, doc_id: str, a: int, b: int) -> list[DiffRow]:
        return diff_items(
            self.version(company, doc_id, a).items, self.version(company, doc_id, b).items
        )

    def rollback(
        self, company: str, doc_id: str, to_version: int, reason: str, author: str, today: date
    ) -> RollbackResult:
        if not reason.strip():
            raise DocError("reason", "Укажите причину отката.")
        v = self.version(company, doc_id, to_version)
        if not v.applicable(today):
            raise DocError("not_applicable", "Откатить можно только к версии с приказом и датой.")
        doc = self._doc(company, doc_id)
        doc["override"] = to_version
        self.store.put(K_DOC, company, doc_id, doc)
        affected = self._affected(company, doc_id) if self._affected else 0
        self.audit(
            company,
            author,
            "doc.rollback",
            doc_id,
            to_version=to_version,
            reason=reason.strip(),
            affected_answers=affected,
        )
        return RollbackResult(doc_id, to_version, affected)

    def delete_version(
        self, company: str, doc_id: str, number: int, author: str, today: date
    ) -> None:
        v = self.version(company, doc_id, number)
        act = self.active_version(company, doc_id, today)
        if act is not None and act.number == v.number:
            raise DocError("active", "Эту версию удалить нельзя: она действует.")
        self.store.delete(K_VER, company, f"{doc_id}:{number}")
        self.audit(company, author, "doc.delete_version", doc_id, version=number)

    def edit_item(
        self, company: str, doc_id: str, base: int, item_number: str, new_text: str, author: str
    ) -> Version:
        """«Исправить пункт»: новая версия-проект с заменённым текстом пункта."""
        if not new_text.strip():
            raise DocError("empty_text", "Итоговый текст пункта обязателен.")
        v = self.version(company, doc_id, base)
        if v.item(item_number) is None:
            raise DocError("no_item", f"Пункта {item_number} нет.")
        items = tuple(
            Item(i.number, new_text.strip(), i.heading) if i.number == item_number else i
            for i in v.items
        )
        nv = self._new_version(company, doc_id, items, v.filename, author, base)
        self.audit(
            company, author, "doc.edit_item", doc_id, base=base, new=nv.number, item=item_number
        )
        return nv
