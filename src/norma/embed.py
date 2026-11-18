"""Эмбеддинги: intfloat/multilingual-e5-small на CPU (+ детерминированный хэш-эмбеддер для тестов)."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

from norma.db import EMBED_DIM

MODEL_NAME = "intfloat/multilingual-e5-small"


class Embedder(Protocol):
    def embed_passages(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


class E5Embedder:
    """Локальная модель e5-small; префиксы query:/passage: обязательны для e5."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, device="cpu")

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        arr = self._model.encode(
            [f"passage: {t}" for t in texts],
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return np.asarray(arr, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        arr = self._model.encode(
            f"query: {text}", normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(arr, dtype=np.float32)


class HashEmbedder:
    """Мешок слов в хэш-пространстве: быстрый детерминированный эмбеддер для тестов."""

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(EMBED_DIM, dtype=np.float32)
        for w in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(w[:5].encode()).hexdigest(), 16)  # noqa: S324
            v[h % EMBED_DIM] += 1.0
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)
