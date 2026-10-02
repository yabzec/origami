import uuid

import pytest

from app.models import ChunkSource
from app.services import chat_context, rag
from app.services.search import QUERY_MAX_CHARS
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
    monkeypatch.setattr(chat_context, "llm_embed", fake_embed)
    monkeypatch.setattr(rag, "llm_complete", fake_complete)
    return calls


def test_retrieve_grounded_when_similar(session, rag_llm):
    doc = seed_document(
        session, "Bolletta marzo",
        [{"content": "La bolletta della luce di marzo: 42 euro.",
          "embedding": basis_vector(0), "page_number": 2}],
    )
    sources, grounded = rag.retrieve(session, "quanto ho pagato la bolletta di marzo?", [doc.id])
    assert grounded is True
    assert sources[0].n == 1
    assert sources[0].title == "Bolletta marzo"
    assert sources[0].page_number == 2
    assert sources[0].document_id == doc.id


def test_retrieve_not_grounded_when_dissimilar(session, rag_llm):
    doc = seed_document(
        session, "Estraneo",
        [{"content": "Curiosamente irrilevante.", "embedding": basis_vector(9)}],
    )
    sources, grounded = rag.retrieve(session, "chi ha vinto il mondiale 2006?", [doc.id])
    assert grounded is False  # best similarity 0.0 < floor


def test_retrieve_not_grounded_when_empty(session, rag_llm):
    sources, grounded = rag.retrieve(session, "qualsiasi cosa", [uuid.uuid4()])
    assert sources == []
    assert grounded is False


def test_build_messages_numbers_sources(session, rag_llm):
    doc = seed_document(
        session, "Contratto",
        [{"content": "Canone mensile 800 euro.", "embedding": basis_vector(0), "page_number": 5}],
    )
    sources, _ = rag.retrieve(session, "quanto pago di affitto?", [doc.id])
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


def test_stream_chat_event_sequence(session, rag_llm, llm_stub):
    doc = seed_document(
        session, "Doc",
        [{"content": "Contenuto rilevante.", "embedding": basis_vector(0), "source": ChunkSource.summary}],
        summary="Contenuto rilevante.",
    )
    llm_stub["select_ids"] = [str(doc.id)]
    events = list(rag.stream_chat(session, [{"role": "user", "content": "domanda?"}], [], []))
    assert [kind for kind, _ in events] == ["meta", "delta", "delta", "done"]
    meta = events[0][1]
    assert meta["grounded"] is True
    assert meta["sources"][0]["n"] == 1
    assert meta["sources"][0]["document_id"] == str(doc.id)
    assert meta["auto_documents"] == [
        {"id": str(doc.id), "title": "Doc", "document_date": doc.document_date.isoformat()}
    ]
    assert events[1][1]["text"] == "Ecco "


def test_retrieve_restricted_to_document_ids(session, rag_llm):
    inside = seed_document(
        session, "Dentro",
        [
            {"content": "Riassunto dentro.", "embedding": basis_vector(0), "source": ChunkSource.summary},
            {"content": "Testo dentro.", "embedding": basis_vector(0), "page_number": 1},
        ],
    )
    seed_document(session, "Fuori", [{"content": "Testo fuori.", "embedding": basis_vector(0)}])

    sources, grounded = rag.retrieve(session, "testo", [inside.id])
    assert grounded is True
    assert {s.document_id for s in sources} == {inside.id}
    assert len(sources) == 2  # every chunk source of a context document is searchable


def test_retrieve_empty_document_ids_skips_search(session, rag_llm):
    seed_document(session, "Doc", [{"content": "Contenuto.", "embedding": basis_vector(0)}])
    assert rag.retrieve(session, "qualsiasi", []) == ([], False)
    assert rag_llm["embed"] == []


def test_retrieve_grounded_only_by_context_documents(session, rag_llm):
    seed_document(session, "Fuori", [{"content": "molto simile", "embedding": basis_vector(0)}])
    inside = seed_document(session, "Dentro", [{"content": "poco simile", "embedding": basis_vector(9)}])
    sources, grounded = rag.retrieve(session, "domanda", [inside.id])
    assert [s.title for s in sources] == ["Dentro"]
    assert grounded is False


def test_retrieve_truncates_long_query(session, rag_llm):
    doc = seed_document(session, "Bolletta", [{"content": "bolletta", "embedding": basis_vector(0)}])
    rag.retrieve(session, "bolletta " * 3000, [doc.id])
    assert len(rag_llm["embed"][0][0]) == QUERY_MAX_CHARS


def test_trim_history_keeps_last_twelve():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)
    ]
    assert rag.trim_history(history) == history[-12:]


def test_trim_history_drops_oldest_over_char_budget():
    history = [
        {"role": "user", "content": "a" * 2500},
        {"role": "assistant", "content": "b" * 2500},
        {"role": "user", "content": "c" * 2500},
    ]
    assert rag.trim_history(history) == history[1:]
    assert rag.trim_history([{"role": "user", "content": "x" * 7000}]) == []


def test_shortlist_query_joins_previous_user_message():
    messages = [
        {"role": "user", "content": "prima"},
        {"role": "assistant", "content": "risposta"},
        {"role": "user", "content": "seconda"},
    ]
    assert rag.shortlist_query(messages) == "prima\nseconda"
    assert rag.shortlist_query([{"role": "user", "content": "sola"}]) == "sola"


def test_build_messages_inserts_history_before_question():
    history = [{"role": "user", "content": "prima"}, {"role": "assistant", "content": "risposta"}]
    messages = rag.build_messages("seconda?", [], history)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1] == {"role": "user", "content": "prima"}
    assert messages[2] == {"role": "assistant", "content": "risposta"}
    assert messages[-1]["content"].endswith("Question: seconda?")


def test_system_prompt_asks_for_markdown_citations_and_history():
    assert "Markdown" in rag.SYSTEM_PROMPT
    assert "[n]" in rag.SYSTEM_PROMPT
    assert "conversation history" in rag.SYSTEM_PROMPT
