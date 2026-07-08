from secretary.embeddings.embedder import EMBEDDING_DIM, LocalEmbedder, Embedder


def test_embedder_is_lazy_and_handles_empty():
    emb = LocalEmbedder()
    assert emb.dim == EMBEDDING_DIM == 384
    # No model should be loaded yet, and empty input must not trigger a load.
    assert emb.encode_passages([]) == []
    assert emb._model is None


def test_protocol_conformance():
    assert isinstance(LocalEmbedder(), Embedder)


def test_lazy_model_load_is_thread_safe(monkeypatch):
    # `secretary serve` shares one LocalEmbedder across worker threads; concurrent
    # first use must load the ONNX model exactly once (it is ~0.2GB — a double load
    # can OOM the documented 1GB-VM target).
    import sys
    import threading
    import time
    import types

    loads = []

    class FakeTextEmbedding:
        def __init__(self, model_name):
            time.sleep(0.05)  # widen the race window
            loads.append(model_name)

    fake = types.ModuleType("fastembed")
    fake.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake)

    emb = LocalEmbedder()
    barrier = threading.Barrier(2)

    def touch():
        barrier.wait()
        _ = emb.model

    threads = [threading.Thread(target=touch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1
