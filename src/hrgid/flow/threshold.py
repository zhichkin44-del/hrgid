""" "No answer" threshold (§5 item 5): chosen on the tuning part and recorded in ADR-0006.

The main part of the set is not used for the choice.
"""

from __future__ import annotations

from collections.abc import Sequence


def pick_threshold(answerable: Sequence[float], unanswerable: Sequence[float]) -> float:
    """Threshold on the reranker score of the top fragment.

    Maximizes the number of correctly handled questions on the tuning part: a score below the
    threshold is
    a refusal. On a tie, the smaller threshold is taken (better to answer than to refuse).
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
