"""Отправка писем с кодом входа. Реального SMTP нет: интерфейс и заглушки.

Код входа — секрет: в журнал приложения он не пишется, только факт отправки на замаскированный адрес.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger("hrgid.panel.mail")


def mask_email(addr: str) -> str:
    name, _, domain = addr.partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


@dataclass
class MemoryMailer:
    """Для тестов: письма лежат в памяти."""

    sent: list[tuple[str, str, str]] = field(default_factory=list)

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def last_code(self, to: str) -> str:
        for addr, _, body in reversed(self.sent):
            if addr == to:
                digits = [w for w in body.split() if w.isdigit() and len(w) == 6]
                if digits:
                    return digits[0]
        raise LookupError("письма с кодом нет")


class LogMailer:
    """Заглушка боевого запуска без SMTP: пишет в журнал только факт отправки."""

    def send(self, to: str, subject: str, body: str) -> None:
        log.info("письмо «%s» отправлено на %s", subject, mask_email(to))


class FileMailer:
    """Локальная разработка: письмо — текстовый файл в каталоге (код виден только там)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def send(self, to: str, subject: str, body: str) -> None:
        n = len(list(self.directory.glob("*.txt"))) + 1
        (self.directory / f"{n:05d}.txt").write_text(
            f"Кому: {to}\nТема: {subject}\n\n{body}\n", encoding="utf-8"
        )
        log.info("письмо «%s» записано в файл для %s", subject, mask_email(to))


class ConsoleMailer:
    """Запуск для владельца без почты: письмо с кодом печатается в консоль (только локально)."""

    def send(self, to: str, subject: str, body: str) -> None:
        print(f"\n[письмо для {to}] {subject}\n{body}\n", flush=True)
