"""Configurable embedding model with a fail-fast dimension guard.

The SurrealDB HNSW index is fixed at 384 dimensions, so a model that emits a
different width would silently corrupt vector search. The guard rejects a wrong-dim
model at construction (cheap registry lookup, no model download), and a final
defensive check at first encode covers models absent from the registry.
"""

from __future__ import annotations

import pytest

from secretary.config import Settings
from secretary.embeddings.embedder import (
    DEFAULT_MODEL,
    EMBEDDING_DIM,
    LocalEmbedder,
    make_embedder,
)


def _settings(**kw) -> Settings:
    base = dict(github_repos="o/r", surreal_url="ws://x", surreal_user="r",
                surreal_pass="r", surreal_ns="n", surreal_db="d")
    base.update(kw)
    return Settings(**base)


def test_default_model_constructs_at_384():
    emb = LocalEmbedder()
    assert emb.dim == EMBEDDING_DIM == 384
    assert emb.model_name == DEFAULT_MODEL


def test_known_wrong_dim_model_raises_before_load():
    # bge-large is 1024-dim in fastembed's registry; must fail fast, no download.
    with pytest.raises(ValueError, match="384"):
        LocalEmbedder("BAAI/bge-large-en-v1.5")


def test_make_embedder_honors_settings():
    emb = make_embedder(_settings())
    assert emb.model_name == DEFAULT_MODEL  # default
    with pytest.raises(ValueError):
        make_embedder(_settings(embedding_model="BAAI/bge-large-en-v1.5"))


def test_unknown_model_constructs_then_guards_on_first_encode(monkeypatch):
    # A model name absent from the registry can't be checked up front, so the guard
    # defers to the first encode: a wrong-width vector must raise before being returned.
    emb = LocalEmbedder("some/unlisted-model")
    assert emb.dim == 384

    import numpy as np

    class _FakeModel:
        def embed(self, texts):
            return [np.zeros(7) for _ in texts]  # 7-dim, wrong

    monkeypatch.setattr(type(emb), "model", property(lambda self: _FakeModel()))
    with pytest.raises(ValueError, match="384"):
        emb.encode_query("hello")


def test_settings_has_embedding_model_env():
    assert _settings(embedding_model="x/y").embedding_model == "x/y"
