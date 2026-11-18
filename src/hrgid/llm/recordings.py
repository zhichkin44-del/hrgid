"""Записи ответов модели (ADR-0003). Ключ = хэш(промпт + модель + версия набора).

replay (CI): записи нет — MissingRecording, тест красный (намеренно).
record (локально, ключ владельца): вызывает настоящую модель и сохраняет ответ.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hrgid.llm.base import LLMClient, LLMRequest, LLMResponse, Tier, ToolCall

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "tests" / "recordings"


class MissingRecording(AssertionError):
    """Нет записи для этого промпта: выполните `make record` и закоммитьте tests/recordings."""


def recording_key(req: LLMRequest, model: str, dataset_version: str) -> str:
    payload = json.dumps(
        {"req": asdict(req), "model": model, "dataset": dataset_version},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load(d: dict[str, Any]) -> LLMResponse:
    calls = tuple(ToolCall(**c) for c in d.get("tool_calls", []))
    return LLMResponse(
        text=d["text"],
        model=d["model"],
        tool_calls=calls,
        input_tokens=d["input_tokens"],
        output_tokens=d["output_tokens"],
    )


class RecordingLLM:
    def __init__(
        self,
        inner: LLMClient | None,
        dataset_version: str,
        directory: Path = DEFAULT_DIR,
        record: bool = False,
        model_name: str | None = None,
    ) -> None:
        if record and inner is None:
            raise ValueError("для записи нужна настоящая модель")
        self.inner = inner
        self.dataset_version = dataset_version
        self.directory = directory
        self.record = record
        self.name = inner.name if inner else (model_name or "recorded")
        self._model_name = model_name or self.name

    def model_for(self, tier: Tier) -> str:
        return self.inner.model_for(tier) if self.inner else f"{self._model_name}-{tier}"

    def complete(self, req: LLMRequest) -> LLMResponse:
        key = recording_key(req, self.model_for(req.tier), self.dataset_version)
        path = self.directory / f"{key}.json"
        if path.exists():
            return _load(json.loads(path.read_text(encoding="utf-8")))
        if not self.record or self.inner is None:
            raise MissingRecording(
                f"нет записи {key[:12]} ({req.purpose}); промпт изменился? выполните make record"
            )
        resp = self.inner.complete(req)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(resp), ensure_ascii=False, indent=1), encoding="utf-8")
        return resp
