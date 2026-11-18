"""Порог «ответа нет» (§5 п.5): подбирается на настроечной части, фиксируется в ADR-0006.

Основная часть набора для подбора не используется.
"""

from __future__ import annotations

from collections.abc import Sequence


def pick_threshold(answerable: Sequence[float], unanswerable: Sequence[float]) -> float:
    """Порог по оценке переранжировщика верхнего фрагмента.

    Максимизирует число верно обработанных вопросов на настроечной части: оценка ниже порога —
    отказ. При равенстве берётся меньший порог (лучше ответить, чем отказать).
    """
    if not answerable and not unanswerable:
        raise ValueError("пустая настроечная часть")
    candidates = sorted({*answerable, *unanswerable})
    best_t, best_score = candidates[0], -1
    for t in [candidates[0] - 1e-9, *candidates]:
        ok = sum(1 for s in answerable if s >= t) + sum(1 for s in unanswerable if s < t)
        if ok > best_score:
            best_t, best_score = t, ok
    return best_t
