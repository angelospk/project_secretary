"""embed_pending / embed_new: incremental, idempotent, and lazy (no model load when
there is nothing to embed). Driven by fakes so no real ONNX model is touched."""

from __future__ import annotations

from secretary.embeddings import service


class FakeDB:
    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self.rows = rows
        self.embedded: dict[tuple, list[float]] = {}

    def query(self, sql: str, params: dict | None = None):
        if "WHERE embedding IS NONE" in sql:
            for kind in ("issue", "pr"):
                if f"FROM {kind} " in sql:
                    return list(self.rows.get(kind, []))
            return []
        if sql.strip().startswith("UPDATE") and "SET embedding" in sql:
            assert params is not None
            self.embedded[(params["kind"], params["repo"], params["n"])] = params["e"]
            return []
        raise AssertionError(f"unexpected query: {sql!r}")


class CountingEmbedder:
    """Stand-in for the real model: counts how often it is invoked."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.calls = 0

    def encode_passages(self, texts):
        self.calls += 1
        return [[0.1] * self.dim for _ in texts]

    def encode_query(self, text):
        return [0.1] * self.dim


def test_embed_pending_no_rows_never_calls_model():
    db = FakeDB({"issue": [], "pr": []})
    emb = CountingEmbedder()
    assert service.embed_pending(db, emb) == {"issue": 0, "pr": 0}
    assert emb.calls == 0


def test_embed_pending_embeds_only_unembedded_rows():
    db = FakeDB({"issue": [{"repo": "o/r", "number": 7, "title": "t", "body": "b"}], "pr": []})
    emb = CountingEmbedder()
    assert service.embed_pending(db, emb) == {"issue": 1, "pr": 0}
    assert ("issue", "o/r", 7) in db.embedded
    assert emb.calls == 1


def test_embed_new_with_no_work_is_a_noop():
    # Builds a (lazy) LocalEmbedder but never encodes, so no model load happens.
    db = FakeDB({"issue": [], "pr": []})
    assert service.embed_new(db) == {"issue": 0, "pr": 0}
