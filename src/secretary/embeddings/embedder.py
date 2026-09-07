"""Embedding models behind a small protocol so the backend is swappable.

Default is `paraphrase-multilingual-MiniLM-L12-v2` via **fastembed** (ONNX runtime,
no PyTorch): 384-dim, multilingual (handles the Greek + English mix in OpenCouncil),
~0.22GB, comfortable on a 1GB VM. The model is symmetric, so passages and queries
are embedded the same way (no e5-style prefixes). Vectors are L2-normalized.

A future API-backed embedder (e.g. Gemini) implements the same protocol.
"""

from __future__ import annotations

import math
import threading
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


def _registry_dim(model_name: str) -> int | None:
    """The model's output dimension per fastembed's local registry, or None if unlisted.

    Reads `TextEmbedding.list_supported_models()` — local metadata, no network and no
    model download — so it is cheap enough to run at construction time.
    """
    try:
        from fastembed import TextEmbedding

        for meta in TextEmbedding.list_supported_models():
            if meta.get("model") == model_name:
                dim = meta.get("dim")
                return int(dim) if dim is not None else None
    except Exception:  # noqa: BLE001 — registry unavailable ⇒ defer to the encode-time guard
        return None
    return None


class LocalEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, *, expected_dim: int = EMBEDDING_DIM):
        # Fail fast: the HNSW index is fixed at `expected_dim`, so a wrong-width model
        # would silently corrupt vector search. Reject it before any (expensive) load.
        declared = _registry_dim(model_name)
        if declared is not None and declared != expected_dim:
            raise ValueError(
                f"embedding model {model_name!r} is {declared}-dim, but the store and HNSW "
                f"index are {expected_dim}-dim. Only {expected_dim}-dim models are supported. "
                f"Switching models invalidates stored vectors — pick a {expected_dim}-dim "
                f"model and re-run `secretary embed`."
            )
        self.model_name = model_name
        self.dim = expected_dim
        self._dim_checked = declared is not None  # registry already vouched for the width
        self._model = None  # lazy: model load + ONNX session is non-trivial
        self._load_lock = threading.Lock()  # serve shares one embedder across workers

    @property
    def model(self):
        # Double-checked locking: without it, concurrent first use (webhook worker
        # threads) loads the ~0.2GB ONNX model twice — enough to OOM a small VM.
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(self.model_name)
        return self._model

    def _guard_dim(self, vec: list[float]) -> list[float]:
        """Last-line check for models absent from the registry: width must match once."""
        if not self._dim_checked:
            if len(vec) != self.dim:
                raise ValueError(
                    f"embedding model {self.model_name!r} produced {len(vec)}-dim vectors, "
                    f"but the store and HNSW index are {self.dim}-dim. Use a {self.dim}-dim model."
                )
            self._dim_checked = True
        return vec

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [_normalize(self._guard_dim(v.tolist())) for v in self.model.embed(list(texts))]

    def encode_query(self, text: str) -> list[float]:
        vec = next(iter(self.model.embed([text])))
        return _normalize(self._guard_dim(vec.tolist()))


def make_embedder(settings) -> LocalEmbedder:
    """Construct the configured embedder (applies the dimension guard)."""
    return LocalEmbedder(settings.embedding_model)
