import pytest

from app.services import rag
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def rag_llm(monkeypatch):
    calls = {"embed": [], "complete": []}

    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [basis_vector(0)]

    def fake_complete(messages, stream=False):
        calls["complete"].append(messages)
        assert stream is True
        return iter(["Ecco ", "la risposta [1]."])

    monkeypatch.setattr(rag, "llm_embed", fake_embed)
    monkeypatch.setattr(rag, "llm_complete", fake_complete)
    return calls


def test_retrieve_grounded_when_similar(session, rag_llm):
    doc = seed_document(
        session, "Bolletta marzo",
        [{"content": "La bolletta della luce di marzo: 42 euro.",
          "embedding": basis_vector(0), "page_number": 2}],
    )
    sources, grounded = rag.retrieve(session, "quanto ho pagato la bolletta di marzo?")
    assert grounded is True
    assert sources[0].n == 1
    assert sources[0].title == "Bolletta marzo"
    assert sources[0].page_number == 2
    assert sources[0].document_id == doc.id


def test_retrieve_not_grounded_when_dissimilar(session, rag_llm):
    seed_document(
        session, "Estraneo",
        [{"content": "Curiosamente irrilevante.", "embedding": basis_vector(9)}],
    )
    sources, grounded = rag.retrieve(session, "chi ha vinto il mondiale 2006?")
    assert grounded is False  # best similarity 0.0 < floor


def test_retrieve_not_grounded_when_empty(session, rag_llm):
    sources, grounded = rag.retrieve(session, "qualsiasi cosa")
    assert sources == []
    assert grounded is False


def test_build_messages_numbers_sources(session, rag_llm):
    seed_document(
        session, "Contratto",
        [{"content": "Canone mensile 800 euro.", "embedding": basis_vector(0), "page_number": 5}],
    )
    sources, _ = rag.retrieve(session, "quanto pago di affitto?")
    messages = rag.build_messages("quanto pago di affitto?", sources)
    assert messages[0]["role"] == "system"
    assert "[n]" in messages[0]["content"] or "[1]" in messages[1]["content"]
    user = messages[1]["content"]
    assert "[1] Contratto (p. 5)" in user
    assert "Canone mensile 800 euro." in user
    assert "quanto pago di affitto?" in user


def test_build_messages_no_sources():
    messages = rag.build_messages("domanda", [])
    assert "No relevant documents" in messages[1]["content"]


def test_stream_answer_event_sequence(session, rag_llm):
    seed_document(
        session, "Doc",
        [{"content": "Contenuto rilevante.", "embedding": basis_vector(0)}],
    )
    events = list(rag.stream_answer(session, "domanda?"))
    kinds = [kind for kind, _ in events]
    assert kinds == ["meta", "delta", "delta", "done"]
    meta = events[0][1]
    assert meta["grounded"] is True
    assert meta["sources"][0]["n"] == 1
    assert isinstance(meta["sources"][0]["document_id"], str)
    assert events[1][1]["text"] == "Ecco "
