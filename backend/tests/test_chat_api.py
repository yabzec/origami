import json

import pytest

from app.services import rag
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def chat_llm(monkeypatch):
    monkeypatch.setattr(rag, "llm_embed", lambda texts: [basis_vector(0)])
    monkeypatch.setattr(
        rag, "llm_complete", lambda messages, stream=False: iter(["Ecco ", "la risposta [1]."])
    )


def parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def test_chat_streams_meta_deltas_done(auth_client, session, chat_llm):
    seed_document(
        session, "Bolletta",
        [{"content": "Bolletta di marzo: 42 euro.", "embedding": basis_vector(0), "page_number": 1}],
    )
    resp = auth_client.post("/api/chat", json={"question": "quanto ho pagato?"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    assert [e["type"] for e in events] == ["meta", "delta", "delta", "done"]
    assert events[0]["grounded"] is True
    assert events[0]["sources"][0]["title"] == "Bolletta"
    assert events[1]["text"] == "Ecco "


def test_chat_ungrounded_flag(auth_client, session, chat_llm):
    resp = auth_client.post("/api/chat", json={"question": "chi ha vinto il mondiale?"})
    events = parse_sse(resp.text)
    assert events[0]["type"] == "meta"
    assert events[0]["grounded"] is False
    assert events[0]["sources"] == []


def test_chat_empty_question_422(auth_client):
    resp = auth_client.post("/api/chat", json={"question": ""})
    assert resp.status_code == 422


def test_chat_requires_auth(client):
    assert client.post("/api/chat", json={"question": "x"}).status_code == 401


def test_chat_emits_error_event_on_failure(auth_client, monkeypatch):
    from app.services import rag

    def boom(texts):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(rag, "llm_embed", boom)
    resp = auth_client.post("/api/chat", json={"question": "ciao"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "chat_failed"
