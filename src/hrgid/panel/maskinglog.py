"""Маскирование персональных данных в логах и трассах панели (SPEC-09 §9).

Фильтр вешается на логгер `hrgid`: сообщение и аргументы проходят через тот же Masker, что и
вопросы к модели (natasha для ФИО + шаблоны паспорта, СНИЛС, ИНН, телефона, почты).
"""

from __future__ import annotations

import logging
from typing import Any

from hrgid.flow.mask import Masker

_masker: Masker | None = None


def masker() -> Masker:
    global _masker
    if _masker is None:
        _masker = Masker()
    return _masker


def mask_text(text: str) -> str:
    """Текст без персональных данных (метки вместо значений)."""
    return masker().mask(text)[0]


class MaskingFilter(logging.Filter):
    """Заменяет персональные данные в записи журнала приложения до вывода."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # неверные аргументы форматирования: не роняем вывод
            msg = str(record.msg)
        record.msg = mask_text(msg)
        record.args = None
        return True


_installed = False


def install(prefix: str = "hrgid") -> None:
    """Маскировать записи журнала всех логгеров `hrgid.*` фабрикой записей: родительские фильтры
    на дочерние логгеры не действуют, а фабрика срабатывает всегда, до любых обработчиков."""
    global _installed
    if _installed:
        return
    old = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        rec = old(*args, **kwargs)
        if rec.name.startswith(prefix):
            try:
                msg = rec.getMessage()
            except Exception:
                msg = str(rec.msg)
            rec.msg = mask_text(msg)
            rec.args = None
        return rec

    logging.setLogRecordFactory(factory)
    _installed = True


def mask_trace(fields: dict[str, Any]) -> dict[str, Any]:
    """Трасса без ПДн: все строковые поля маскируются."""
    return {k: mask_text(v) if isinstance(v, str) else v for k, v in fields.items()}
