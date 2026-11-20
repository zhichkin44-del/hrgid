"""Ворота качества (§12, ADR-0003): прогон набора на записанных ответах.

Красные ворота — если пороги этапа 2 не достигнуты или для текущих промптов нет записи
(промпт изменили, а `make record` не выполнили). Пока первой живой записи не было
(tests/recordings/PENDING), ворота пропускаются с явным предупреждением, не падают.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hrgid.flow.evalrun import run_eval, summarize, verdict
from hrgid.flow.graph import Pipeline
from hrgid.llm.recordings import MissingRecording

Status = Literal["pass", "fail", "skipped"]
Verdict = Callable[[dict[str, tuple[int, int]]], dict[str, bool]]

PENDING_WARNING = (
    "Ворота качества ПРОПУЩЕНЫ: живой записи ответов модели ещё не было "
    "(tests/recordings/PENDING). Выполните `make record` ключом владельца и удалите PENDING."
)


@dataclass(frozen=True)
class GateResult:
    status: Status
    message: str

    @property
    def ok(self) -> bool:
        return self.status != "fail"


def recordings_pending(root: Path) -> bool:
    return (root / "tests" / "recordings" / "PENDING").exists()


def evaluate_gate(
    pipeline: Pipeline,
    rows: list[dict[str, Any]],
    *,
    pending: bool = False,
    check: Verdict = verdict,
    company_id: str = "studio",
) -> GateResult:
    if pending:
        return GateResult("skipped", PENDING_WARNING)
    try:
        results = run_eval(pipeline, rows, company_id)
    except MissingRecording as exc:
        return GateResult("fail", f"нет записи для текущих промптов: {exc}")
    summary = summarize(results)
    failed = sorted(k for k, ok in check(summary).items() if not ok)
    if failed:
        return GateResult("fail", f"пороги не достигнуты: {', '.join(failed)}; {summary}")
    return GateResult("pass", f"пороги достигнуты: {summary}")
