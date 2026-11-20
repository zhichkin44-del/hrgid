"""Сборка потока для прогона набора: настоящий поиск (БД, e5, переранжировщик) + модель.

Тяжёлые зависимости импортируются внутри функций: модуль безопасно импортируется без них.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from hrgid.flow.graph import FlowConfig, Pipeline
from hrgid.flow.searcher import HybridSearcher
from hrgid.llm.base import LLMClient
from hrgid.llm.claude import ClaudeClient
from hrgid.llm.fake import no_model_client
from hrgid.llm.gigachat import GigaChatClient
from hrgid.llm.recordings import DEFAULT_DIR, RecordingLLM
from hrgid.llm.resilient import ResilientLLM
from hrgid.llm.yandex import YandexGPTClient

ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = ("claude", "gigachat", "yandex")


def dataset_version(eval_file: Path) -> str:
    """Версия набора: хэш файла вопросов. Правка набора -> другие ключи записей."""
    return "eval-" + hashlib.sha256(eval_file.read_bytes()).hexdigest()[:12]


def make_client(provider: str) -> LLMClient:
    if provider == "claude":
        return ClaudeClient()
    if provider == "gigachat":
        return GigaChatClient()
    if provider == "yandex":
        return YandexGPTClient()
    if provider == "fake":  # запуск без ключей: поиск и панель работают, ответы — отказы
        return no_model_client()
    raise ValueError(f"неизвестный провайдер: {provider}")


def make_llm(
    provider: str, eval_file: Path, record: bool, directory: Path = DEFAULT_DIR
) -> RecordingLLM:
    """record=True — живой вызов с ключом владельца и запись; иначе только воспроизведение."""
    client = make_client(provider)
    inner: LLMClient = ResilientLLM(client) if record else client
    return RecordingLLM(inner, dataset_version(eval_file), directory, record=record)


def make_searcher(conn: Any, reranker_key: str) -> HybridSearcher:
    from hrgid.embed import E5Embedder
    from hrgid.rerank import CANDIDATES, CrossEncoderReranker

    return HybridSearcher(conn, E5Embedder(), CrossEncoderReranker(CANDIDATES[reranker_key]))


def make_pipeline(llm: LLMClient, searcher: HybridSearcher, threshold: float | None) -> Pipeline:
    return Pipeline(llm, llm, searcher, config=FlowConfig(no_answer_threshold=threshold))
