import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services.embedding_check import EmbeddingCheckError


def test_api_lifespan_runs_guard(monkeypatch):
    def boom():
        raise EmbeddingCheckError("Embedding model changed.")

    monkeypatch.setattr(main, "embedding_startup_guard", boom)
    with pytest.raises(EmbeddingCheckError):
        with TestClient(main.app):
            pass


def test_worker_main_stops_before_loop(monkeypatch):
    import app.worker.__main__ as worker_main

    started = []

    def boom(engine):
        raise EmbeddingCheckError("Embedding model changed.")

    monkeypatch.setattr(worker_main, "startup_guard", boom)
    monkeypatch.setattr(worker_main, "main_loop", lambda engine: started.append(engine))
    with pytest.raises(EmbeddingCheckError):
        worker_main.main(engine=object())
    assert started == []
