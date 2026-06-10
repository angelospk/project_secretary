"""Embedding models behind a small protocol so the backend is swappable.

Default is `paraphrase-multilingual-MiniLM-L12-v2` via **fastembed** (ONNX runtime,
no PyTorch): 384-dim, multilingual (handles the Greek + English mix in OpenCouncil),
~0.22GB, comfortable on a 1GB VM. The model is symmetric, so passages and queries
are embedded the same way (no e5-style prefixes). Vectors are L2-normalized.

A future API-backed embedder (e.g. Gemini) implements the same protocol.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def encode_passages(self, texts: list[str]) -> list[list[float]]: ...

    def encode_query(self, text: str) -> list[float]: ...


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class LocalEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self.dim = EMBEDDING_DIM
        self._model = None  # lazy: model load + ONNX session is non-trivial

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name)
        return self._model

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [_normalize(v.tolist()) for v in self.model.embed(list(texts))]

    def encode_query(self, text: str) -> list[float]:
        vec = next(iter(self.model.embed([text])))
        return _normalize(vec.tolist())
