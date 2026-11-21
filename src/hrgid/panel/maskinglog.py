"""Masking of personal data in the logs and traces of the panel (SPEC-09 §9).

The filter is attached to the `hrgid` logger: the message and arguments pass through the same Masker as
questions to the model (natasha for full names, templates for passport, SNILS, TIN, phone and email).
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
    """Text without personal data (labels instead of values)."""
    return masker().mask(text)[0]


class MaskingFilter(logging.Filter):
    """Replaces personal data in an application log record before output."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # wrong formatting arguments: do not break the output
            msg = str(record.msg)
        record.msg = mask_text(msg)
        record.args = None
        return True


_installed = False


def install(prefix: str = "hrgid") -> None:
    """Masks the records of the logs of all `hrgid.*` loggers with a record factory: parent filters
    do not apply to child loggers, while the factory always runs, before any handlers.
    """
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
    """A trace without personal data: all string fields are masked."""
    return {k: mask_text(v) if isinstance(v, str) else v for k, v in fields.items()}
