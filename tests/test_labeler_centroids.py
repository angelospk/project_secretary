"""Centroid building: description + example mean, normalized, taxonomy-hash cached."""

from __future__ import annotations

import math

from secretary.db import repo as db_repo
from secretary.labeler import centroids as centroids_mod
from secretary.labeler.centroids import build_centroids
from secretary.labeler.taxonomy import Category, Taxonomy


class FakeEmbedder:
    dim = 2

    def __init__(self):
        self.calls = 0

    def encode_passages(self, texts):
        self.calls += len(texts)
        return [[1.0, 0.0] for _ in texts]

    def encode_query(self, text):
        return [1.0, 0.0]


def _taxonomy() -> Taxonomy:
    cat = Category(key="notif", description="delivery", label="notif", examples=(412,))
    return Taxonomy(categories=(cat,), hash="h1")


def test_centroid_is_normalized_mean_of_description_and_examples(monkeypatch):
    kv: dict = {}
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))
    # example #412's stored embedding points the other way.
    monkeypatch.setattr(db_repo, "get_embedding", lambda db, kind, repo, n: [0.0, 1.0])

    centroids = build_centroids(None, FakeEmbedder(), "o/r", _taxonomy())
    assert len(centroids) == 1
    vx, vy = centroids[0].vector
    # mean([1,0],[0,1]) = [0.5,0.5] → normalized to [0.707, 0.707].
    assert math.isclose(vx, 1 / math.sqrt(2), abs_tol=1e-6)
    assert math.isclose(vy, 1 / math.sqrt(2), abs_tol=1e-6)


def test_cache_hit_skips_recompute(monkeypatch):
    kv: dict = {}
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))
    monkeypatch.setattr(db_repo, "get_embedding", lambda db, kind, repo, n: [0.0, 1.0])

    embedder = FakeEmbedder()
    build_centroids(None, embedder, "o/r", _taxonomy())
    assert embedder.calls == 1  # encoded the description once

    embedder2 = FakeEmbedder()
    out = build_centroids(None, embedder2, "o/r", _taxonomy())
    assert embedder2.calls == 0  # served from cache, no re-encode
    assert out[0].label == "notif"


def test_taxonomy_edit_invalidates_cache(monkeypatch):
    kv: dict = {}
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))
    monkeypatch.setattr(db_repo, "get_embedding", lambda db, kind, repo, n: [0.0, 1.0])

    build_centroids(None, FakeEmbedder(), "o/r", _taxonomy())
    edited = Taxonomy(categories=_taxonomy().categories, hash="h2")  # different hash
    embedder = FakeEmbedder()
    build_centroids(None, embedder, "o/r", edited)
    assert embedder.calls == 1  # recomputed under the new hash
