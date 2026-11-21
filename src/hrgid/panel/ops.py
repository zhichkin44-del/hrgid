"""Panel operational data: technical settings, question log, spending and budget.

The question log (`qlog`) holds only masked text and numbers: cost, latency, route.
П7 (analytics) and П8 (spending, speed, failures) are computed from it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from hrgid.bot import hours
from hrgid.bot.repo import Repo
from hrgid.bot.store import DocStore
from hrgid.flow.graph import AskRequest, FlowAnswer
from hrgid.flow.trace import TraceRecord, TraceSink

K_TECH, K_QLOG = "ptech", "qlog"

Provider = Literal["gigachat", "yandex", "claude", "openrouter"]
FOREIGN = ("claude", "openrouter")  # transfer across the border: only through a warning (П9.5)
OverAction = Literal["queue", "refuse"]
PROVIDER_NAMES: dict[str, str] = {
    "gigachat": "GigaChat",
    "yandex": "YandexGPT",
    "claude": "Claude",
    "openrouter": "OpenRouter",
}
OVER_ACTIONS: dict[str, str] = {
    "queue": "Новые вопросы уходят в очередь HR, база ответов и правила работают",
    "refuse": "Помощник вежливо отказывает и даёт контакт HR, база и правила работают",
}
MIN_LIMIT = 1000.0

TOPICS: dict[str, tuple[str, ...]] = {
    "Отпуск": ("отпуск", "отпускн", "отгул"),
    "Зарплата и выплаты": ("зарплат", "аванс", "выплат", "оплат", "оклад", "премия"),
    "Больничный": ("больнич", "справк", "болезн"),
    "График и удалёнка": ("график", "удален", "удалён", "дистанц", "смен"),
    "Сверхурочные и выходные": ("сверхуроч", "выходн", "переработ", "празднич"),
    "Увольнение": ("увольн", "компенсац", "расчёт при", "последн"),
}


def topic_of(text: str) -> str:
    low = text.lower()
    for name, words in TOPICS.items():
        if any(w in low for w in words):
            return name
    return "Другое"


class TechSettings(BaseModel):
    """Company technical settings (П8, П9; changed only by the administrator)."""

    provider: Provider = "gigachat"
    claude_ack: bool = False  # "Employees notified" (П9.5 · warning)
    budget_rub: float = 10000.0
    over_action: OverAction = "queue"
    retention_days: int = 365
    greeting: str = (
        "Здравствуйте! Я помогу с кадровыми вопросами по Трудовому кодексу и правилам компании."
    )
    escalation: str = "Если HR не ответил в срок — обращение уходит администратору."


class OpsStore:
    def __init__(self, store: DocStore, repo: Repo, clock: Callable[[], datetime]) -> None:
        self.store = store
        self.repo = repo
        self.clock = clock

    # --- technical settings ---
    def tech(self, company: str) -> TechSettings:
        d = self.store.get(K_TECH, company, "main")
        return TechSettings.model_validate(d) if d else TechSettings()

    def save_tech(self, company: str, t: TechSettings) -> None:
        self.store.put(K_TECH, company, "main", t.model_dump(mode="json"))

    # --- question log ---
    def log_question(
        self,
        company: str,
        *,
        masked_question: str,
        route: str = "law",
        status: str = "answered",
        cost_rub: float = 0.0,
        latency_ms: int = 0,
        cache_hit: bool = False,
        model: str = "",
        provider_error: bool = False,
        by: str = "ai",
        now: datetime | None = None,
    ) -> None:
        at = now or self.clock()
        n = self.store.next_seq(company, "qlog")
        self.store.put(
            K_QLOG,
            company,
            f"{n:010d}",
            {
                "at": at.isoformat(),
                "topic": topic_of(masked_question),
                "route": route,
                "status": status,
                "by": by,
                "masked_question": masked_question,
                "cost_rub": cost_rub,
                "latency_ms": latency_ms,
                "cache_hit": cache_hit,
                "model": model,
                "provider_error": provider_error,
            },
        )

    def questions(self, company: str) -> list[dict[str, Any]]:
        return self.store.all(K_QLOG, company)

    # --- spending ---
    def month_spend(self, company: str, now: datetime) -> float:
        s = self.repo.settings(company)
        local = hours.to_local(now, s)
        total = 0.0
        for q in self.questions(company):
            at = hours.to_local(datetime.fromisoformat(q["at"]), s)
            if (at.year, at.month) == (local.year, local.month):
                total += float(q["cost_rub"])
        return round(total, 4)

    def budget(self, company: str, now: datetime) -> BudgetStatus:
        t = self.tech(company)
        spent = self.month_spend(company, now)
        return BudgetStatus(spent, t.budget_rub, spent >= t.budget_rub > 0, t.over_action)


@dataclass(frozen=True)
class BudgetStatus:
    spent: float
    limit: float
    exhausted: bool
    action: OverAction

    @property
    def pct(self) -> int:
        return min(100, int(self.spent / self.limit * 100)) if self.limit else 0


class PanelTraceSink:
    """Sink for the traces of the answer flow: writes to the panel's question log and passes them on (DB)."""

    def __init__(self, ops: OpsStore, inner: TraceSink | None = None) -> None:
        self.ops = ops
        self.inner = inner

    def write(self, rec: TraceRecord) -> None:
        self.ops.log_question(
            rec.tenant_id,
            masked_question=rec.masked_question,
            route=rec.route,
            status=rec.status,
            cost_rub=rec.cost_rub,
            latency_ms=rec.latency_ms,
            cache_hit=rec.cache_hit,
            model=",".join(rec.models),
            provider_error=rec.status == "error",
        )
        if self.inner is not None:
            self.inner.write(rec)


class Answerer:  # minimal interface, as in bot.engine
    def ask(self, req: AskRequest) -> FlowAnswer:  # pragma: no cover - protocol
        raise NotImplementedError


class BudgetGatedAnswerer:
    """Scenario 5: budget exhausted, the model is not called, the action is chosen in П9/П8."""

    def __init__(
        self, inner: Any, ops: OpsStore, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.inner = inner
        self.ops = ops
        self.clock = clock or (lambda: datetime.now(UTC))

    def ask(self, req: AskRequest) -> FlowAnswer:
        b = self.ops.budget(req.company_id, self.clock())
        if b.exhausted:
            return FlowAnswer("to_hr" if b.action == "queue" else "no_answer", "")
        ans: FlowAnswer = self.inner.ask(req)
        return ans


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = max(0, min(len(v) - 1, round(p / 100 * (len(v) - 1))))
    return float(v[k])
