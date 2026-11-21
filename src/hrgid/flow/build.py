"""Flow assembly for running the set: real search (DB, e5, reranker) + model.

Heavy dependencies are imported inside functions, so the module is safe to import without them.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from hrgid.flow.graph import FlowConfig, Pipeline
from hrgid.flow.searcher import HybridSearcher
from hrgid.llm.base import LLMClient
from hrgid.llm.claude import ClaudeClient
from hrgid.llm.fake import no_model_client
from hrgid.llm.gigachat import GigaChatClient
from hrgid.llm.openrouter import OpenRouterClient
from hrgid.llm.recordings import DEFAULT_DIR, RecordingLLM
from hrgid.llm.resilient import ResilientLLM
from hrgid.llm.yandex import YandexGPTClient

ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = ("openrouter", "claude", "gigachat", "yandex")


def dataset_version(eval_file: Path) -> str:
    """Set version: hash of the questions file. Editing the set gives other recording keys."""
    return "eval-" + hashlib.sha256(eval_file.read_bytes()).hexdigest()[:12]


def provider_from_env(default: str) -> str:
    """HRGID_LLM_PROVIDER (openrouter|claude|gigachat|yandex|fake); the old HRGID_PROVIDER also
    works."""
    return os.environ.get("HRGID_LLM_PROVIDER") or os.environ.get("HRGID_PROVIDER") or default


def make_client(provider: str) -> LLMClient:
    if provider == "openrouter":
        return OpenRouterClient()
    if provider == "claude":
        return ClaudeClient()
    if provider == "gigachat":
        return GigaChatClient()
    if provider == "yandex":
        return YandexGPTClient()
    if provider == "fake":  # run without keys: search and the panel work, answers are refusals
        return no_model_client()
    raise ValueError(f"неизвестный провайдер: {provider}")


def make_llm(
    provider: str, eval_file: Path, record: bool, directory: Path = DEFAULT_DIR
) -> RecordingLLM:
    """record=True: a live call with the owner's key and recording; otherwise replay only."""
    client = make_client(provider)
    inner: LLMClient = ResilientLLM(client) if record else client
    return RecordingLLM(inner, dataset_version(eval_file), directory, record=record)


def make_searcher(conn: Any, reranker_key: str) -> HybridSearcher:
    from hrgid.embed import E5Embedder
    from hrgid.rerank import CANDIDATES, CrossEncoderReranker

    return HybridSearcher(conn, E5Embedder(), CrossEncoderReranker(CANDIDATES[reranker_key]))


def make_pipeline(llm: LLMClient, searcher: HybridSearcher, threshold: float | None) -> Pipeline:
    return Pipeline(llm, llm, searcher, config=FlowConfig(no_answer_threshold=threshold))
