from secretary.embeddings.embedder import EMBEDDING_DIM, LocalEmbedder, Embedder


def test_embedder_is_lazy_and_handles_empty():
    emb = LocalEmbedder()
    assert emb.dim == EMBEDDING_DIM == 384
    # No model should be loaded yet, and empty input must not trigger a load.
    assert emb.encode_passages([]) == []
    assert emb._model is None


def test_protocol_conformance():
    assert isinstance(LocalEmbedder(), Embedder)
