"""Журнал действий панели (П10): только факт действия, текст переписки не хранится (§0 п.7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hrgid.bot.store import DocStore

K_AUDIT = "paudit"

LABELS: dict[str, str] = {
    "ticket.answer": "Ответил на обращение",
    "ticket.assign": "Назначил обращение коллеге",
    "ticket.reveal": "Раскрыл автора обращения",
    "kb.add": "Добавил ответ в базу",
    "kb.edit": "Исправил ответ в базе",
    "kb.archive": "Отправил ответ в архив",
    "kb.restore": "Вернул ответ из архива",
    "kb.review": "Пересмотрел ответы базы",
    "rating.resolve": "Разобрал оценку",
    "doc.upload": "Загрузил документ",
    "doc.order": "Указал приказ и дату начала",
    "doc.rollback": "Откатил документ к версии",
    "doc.delete_version": "Удалил версию документа",
    "tk.check": "Проверил версию на ТК",
    "tk.fix": "Исправил пункт правил",
    "tk.accept_risk": "Принял риск в правилах",
    "tk.pdf": "Скачал отчёт о проверке",
    "emp.invite": "Пригласил сотрудника",
    "emp.import": "Импортировал выгрузку из 1С",
    "emp.codes": "Выдал коды приглашения",
    "emp.newcode": "Выдал новый код",
    "emp.disable": "Отключил доступ сотрудника",
    "emp.enable": "Вернул доступ сотрудника",
    "emp.edit": "Исправил данные сотрудника",
    "emp.purge": "Удалил данные сотрудника",
    "group.add": "Добавил группу",
    "group.delete": "Удалил группу",
    "ann.send": "Отправил объявление",
    "ann.remind": "Напомнил о прочтении",
    "settings.save": "Сохранил настройки",
    "settings.model": "Изменил модель помощника",
    "budget.limit": "Изменил лимит расходов",
    "budget.request": "Попросил администратора изменить лимит",
    "company.export": "Выгрузил данные компании",
    "company.delete": "Удалил данные компании",
    "log.export": "Выгрузил журнал",
}


@dataclass(frozen=True)
class Entry:
    n: str
    at: datetime
    user: str
    action: str
    target: str

    @property
    def label(self) -> str:
        return LABELS.get(self.action, self.action)


class Journal:
    def __init__(self, store: DocStore) -> None:
        self.store = store

    def add(self, company: str, now: datetime, user: str, action: str, target: str = "") -> None:
        n = self.store.next_seq(company, "paudit")
        self.store.put(
            K_AUDIT,
            company,
            f"{n:010d}",
            {
                "n": f"{n:010d}",
                "at": now.isoformat(),
                "user": user,
                "action": action,
                "target": target,
            },
        )

    def entries(self, company: str) -> list[Entry]:
        rows = [
            Entry(d["n"], datetime.fromisoformat(d["at"]), d["user"], d["action"], d["target"])
            for d in self.store.all(K_AUDIT, company)
        ]
        return sorted(rows, key=lambda e: e.n, reverse=True)

    def get(self, company: str, n: str) -> Entry | None:
        for e in self.entries(company):
            if e.n == n:
                return e
        return None
