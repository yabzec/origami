import json
import uuid

import pytest

from app.models import ChunkSource
from app.services import chat_context, rag
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def chat_llm(monkeypatch, llm_stub):
    """Query embedding = basis_vector(0) for shortlist and retrieval; scripted answer stream."""
    calls = {"embed": [], "complete": []}

    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [basis_vector(0) for _ in texts]

    def fake_complete(messages, stream=False):
        calls["complete"].append(messages)
        return iter(["Ecco ", "la risposta [1]."])

    monkeypatch.setattr(rag, "llm_embed", fake_embed)
    monkeypatch.setattr(chat_context, "llm_embed", fake_embed)
    monkeypatch.setattr(rag, "llm_complete", fake_complete)
    return calls


def parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def body(*contents, pinned=(), excluded=()):
    """Alternating user/assistant messages starting with the user; odd count ends with the user."""
    return {
        "messages": [
            {"role": "user" if i % 2 == 0 else "assistant", "content": c}
            for i, c in enumerate(contents)
        ],
        "pinned_ids": [str(i) for i in pinned],
        "excluded_ids": [str(i) for i in excluded],
    }


def seed_doc(session, title, vector, content="Testo del documento."):
    """A document reachable by the shortlist (summary chunk) and by retrieval (content chunk)."""
    return seed_document(
        session, title,
        [
            {"content": f"Riassunto di {title}", "embedding": vector, "source": ChunkSource.summary},
            {"content": content, "embedding": vector, "page_number": 1},
        ],
        summary=f"Riassunto di {title}",
    )


def source_doc_ids(meta: dict) -> set[str]:
    return {s["document_id"] for s in meta["sources"]}


def test_chat_streams_meta_deltas_done(auth_client, session, chat_llm, llm_stub):
    doc = seed_doc(session, "Bolletta", basis_vector(0), "Bolletta di marzo: 42 euro.")
    llm_stub["select_ids"] = [str(doc.id)]

    resp = auth_client.post("/api/chat", json=body("quanto ho pagato?"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    assert [e["type"] for e in events] == ["meta", "delta", "delta", "done"]
    meta = events[0]
    assert meta["grounded"] is True
    assert meta["sources"][0]["title"] == "Bolletta"
    assert source_doc_ids(meta) == {str(doc.id)}
    assert meta["auto_documents"] == [
        {"id": str(doc.id), "title": "Bolletta", "document_date": doc.document_date.isoformat()}
    ]
    assert events[1]["text"] == "Ecco "


def test_chat_ungrounded_flag(auth_client, session, chat_llm):
    events = parse_sse(auth_client.post("/api/chat", json=body("chi ha vinto il mondiale?")).text)
    assert events[0]["type"] == "meta"
    assert events[0]["grounded"] is False
    assert events[0]["sources"] == []
    assert events[0]["auto_documents"] == []
    assert events[-1]["type"] == "done"


def test_chat_invalid_messages_422(auth_client):
    for payload in ({"messages": []}, {"messages": [{"role": "user", "content": ""}]}):
        resp = auth_client.post("/api/chat", json=payload)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


def test_chat_requires_auth(client):
    assert client.post("/api/chat", json=body("x")).status_code == 401


def test_chat_emits_error_event_on_failure(auth_client, monkeypatch, chat_llm):
    def boom(texts):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(chat_context, "llm_embed", boom)
    resp = auth_client.post("/api/chat", json=body("ciao"))
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "chat_failed"


def test_chat_last_message_not_user_422(auth_client):
    resp = auth_client.post("/api/chat", json=body("domanda", "risposta"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_chat_more_than_40_messages_422(auth_client):
    resp = auth_client.post("/api/chat", json=body(*[f"m{i}" for i in range(41)]))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_chat_context_is_pinned_plus_auto_minus_excluded(auth_client, session, chat_llm, llm_stub):
    pinned = seed_doc(session, "Fissato", basis_vector(1))
    auto = seed_doc(session, "Automatico", basis_vector(0))
    excluded = seed_doc(session, "Escluso", basis_vector(0))
    llm_stub["select_ids"] = [str(excluded.id), str(auto.id)]  # the preflight even names the excluded one

    resp = auth_client.post(
        "/api/chat", json=body("domanda", pinned=[pinned.id], excluded=[excluded.id])
    )
    meta = parse_sse(resp.text)[0]

    candidate_ids = {c["id"] for c in llm_stub["select"][0]["candidates"]}
    assert str(auto.id) in candidate_ids
    assert str(pinned.id) not in candidate_ids and str(excluded.id) not in candidate_ids
    assert source_doc_ids(meta) == {str(pinned.id), str(auto.id)}
    assert [d["id"] for d in meta["auto_documents"]] == [str(auto.id)]


def test_chat_retrieval_restricted_to_context(auth_client, session, chat_llm, llm_stub):
    outside = seed_doc(session, "Fuori", basis_vector(0), "bolletta luce marzo")
    pinned = seed_doc(session, "Contratto", basis_vector(5), "contratto di affitto")
    llm_stub["select_ids"] = []

    meta = parse_sse(
        auth_client.post("/api/chat", json=body("bolletta luce", pinned=[pinned.id])).text
    )[0]

    assert str(outside.id) in {c["id"] for c in llm_stub["select"][0]["candidates"]}
    assert meta["sources"] and source_doc_ids(meta) == {str(pinned.id)}
    assert meta["grounded"] is False  # only the context's similarity counts


def test_chat_history_trimmed_to_twelve_messages(auth_client, session, chat_llm, llm_stub):
    seed_doc(session, "Doc", basis_vector(0))
    contents = [f"m{i}" for i in range(20)] + ["domanda finale"]

    auth_client.post("/api/chat", json=body(*contents))

    expected = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(8, 20)
    ]
    sent = chat_llm["complete"][0]
    assert sent[0]["role"] == "system"
    assert sent[1:-1] == expected
    assert sent[-1]["role"] == "user"
    assert sent[-1]["content"].endswith("Question: domanda finale")
    assert llm_stub["select"][0]["history"] == expected
    assert llm_stub["select"][0]["question"] == "domanda finale"


def test_chat_preflight_failure_still_streams_answer(auth_client, session, chat_llm, llm_stub):
    seed_doc(session, "Doc", basis_vector(0))
    llm_stub["select_error"] = RuntimeError("provider down")

    events = parse_sse(auth_client.post("/api/chat", json=body("domanda")).text)
    assert [e["type"] for e in events] == ["meta", "delta", "delta", "done"]
    assert events[0]["auto_documents"] == []
    assert events[0]["sources"] == []


def test_chat_unknown_pinned_id_ignored(auth_client, session, chat_llm, llm_stub):
    real = seed_doc(session, "Reale", basis_vector(0))
    deleted_id = uuid.uuid4()

    resp = auth_client.post("/api/chat", json=body("domanda", pinned=[deleted_id, real.id]))
    events = parse_sse(resp.text)
    assert resp.status_code == 200
    assert events[-1]["type"] == "done"
    assert source_doc_ids(events[0]) == {str(real.id)}


def test_chat_drops_preflight_ids_outside_shortlist(auth_client, session, chat_llm, llm_stub):
    doc = seed_doc(session, "Bolletta", basis_vector(0))
    llm_stub["select_ids"] = ["not-a-uuid", str(uuid.uuid4()), str(doc.id), str(doc.id)]

    events = parse_sse(auth_client.post("/api/chat", json=body("domanda")).text)
    assert events[-1]["type"] == "done"
    assert [d["id"] for d in events[0]["auto_documents"]] == [str(doc.id)]
    assert source_doc_ids(events[0]) == {str(doc.id)}


def test_chat_skips_preflight_when_all_candidates_excluded(auth_client, session, chat_llm, llm_stub):
    pinned = seed_doc(session, "Fissato", basis_vector(0))
    excluded = seed_doc(session, "Escluso", basis_vector(1))

    events = parse_sse(
        auth_client.post(
            "/api/chat", json=body("domanda", pinned=[pinned.id], excluded=[excluded.id])
        ).text
    )
    assert llm_stub["select"] == []
    assert events[0]["auto_documents"] == []
    assert source_doc_ids(events[0]) == {str(pinned.id)}
    assert events[-1]["type"] == "done"


def test_chat_shortlist_query_includes_previous_user_message(auth_client, chat_llm):
    auth_client.post("/api/chat", json=body("prima domanda", "risposta", "e quella di marzo?"))
    assert chat_llm["embed"][0] == ["prima domanda\ne quella di marzo?"]
