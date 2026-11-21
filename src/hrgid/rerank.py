"""Local rerankers (cross-models) on CPU."""

from __future__ import annotations

CANDIDATES = {
    "mmarco-minilm": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    "bge-m3": "BAAI/bge-reranker-v2-m3",
}


class CrossEncoderReranker:
    """Raw cross-model score (logit): suitable for the "no answer" threshold."""

    def __init__(self, model_name: str, max_length: int = 384) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, device="cpu", max_length=max_length)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        scores = self._model.predict(
            [(query, t) for t in texts], batch_size=16, show_progress_bar=False
        )
        return [float(s) for s in scores]
