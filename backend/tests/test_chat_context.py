import pytest

from app.models import ChunkSource
from app.services import chat_context
from app.services.chat_context import shortlist_documents
from app.services.search import QUERY_MAX_CHARS
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def embed_calls(monkeypatch):
    """Query embedding = basis_vector(0)."""
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [basis_vector(0) for _ in texts]

    monkeypatch.setattr(chat_context, "llm_embed", fake_embed)
    return calls


def summary_doc(session, title, vector, summary=None, **kwargs):
    text = summary or f"Riassunto di {title}"
    return seed_document(
        session, title,
        [{"content": text, "embedding": vector, "source": ChunkSource.summary}],
        summary=text, **kwargs,
    )


def test_shortlist_ranks_documents_by_summary_and_metadata(session, embed_calls):
    best = summary_doc(session, "Bolletta luce", basis_vector(0), "Bolletta della luce di marzo")
    second = seed_document(
        session, "Contratto",
        [{"content": "Contratto affitto", "embedding": basis_vector(1), "source": ChunkSource.metadata}],
    )
    content_only = seed_document(
        session, "Solo contenuto", [{"content": "bolletta bolletta", "embedding": basis_vector(0)}]
    )

    result = shortlist_documents(session, "bolletta")

    assert [c["id"] for c in result] == [str(best.id), str(second.id)]
    assert str(content_only.id) not in {c["id"] for c in result}
    assert result[0] == {
        "id": str(best.id),
        "title": "Bolletta luce",
        "document_date": best.document_date.isoformat(),
        "doc_type": "text",
        "summary_line": "Bolletta della luce di marzo",
    }
    assert embed_calls == [["bolletta"]]


def test_shortlist_one_entry_per_document_and_limit(session, embed_calls):
    doc = seed_document(
        session, "Doppio",
        [
            {"content": "riassunto doppio", "embedding": basis_vector(0), "source": ChunkSource.summary},
            {"content": "Doppio riassunto", "embedding": basis_vector(0), "source": ChunkSource.metadata},
        ],
    )
    for i in range(4):
        summary_doc(session, f"Doc {i}", basis_vector(i + 1))

    ids = [c["id"] for c in shortlist_documents(session, "riassunto")]
    assert ids[0] == str(doc.id)
    assert len(ids) == len(set(ids)) == 5
    assert len(shortlist_documents(session, "riassunto", limit=3)) == 3


def test_shortlist_summary_line_is_flat_and_capped(session, embed_calls):
    summary_doc(session, "Lungo", basis_vector(0), "Prima riga.\n\nSeconda   riga " + "x" * 400)
    [candidate] = shortlist_documents(session, "lungo")
    assert candidate["summary_line"].startswith("Prima riga. Seconda riga x")
    assert len(candidate["summary_line"]) == 200
    assert "\n" not in candidate["summary_line"]


def test_shortlist_falls_back_to_description(session, embed_calls):
    seed_document(
        session, "Manuale",
        [{"content": "Manuale\n\nIstruzioni lavatrice", "embedding": basis_vector(0),
          "source": ChunkSource.metadata}],
        description="Istruzioni\nlavatrice",
    )
    [candidate] = shortlist_documents(session, "lavatrice")
    assert candidate["summary_line"] == "Istruzioni lavatrice"


def test_shortlist_skips_excluded_ids(session, embed_calls):
    excluded = summary_doc(session, "Escluso", basis_vector(0))
    kept = summary_doc(session, "Tenuto", basis_vector(1))
    result = shortlist_documents(session, "riassunto", exclude_ids={excluded.id})
    assert [c["id"] for c in result] == [str(kept.id)]


def test_shortlist_truncates_long_query(session, embed_calls):
    summary_doc(session, "Bolletta", basis_vector(0))
    shortlist_documents(session, "bolletta " * 3000)
    assert len(embed_calls[0][0]) == QUERY_MAX_CHARS


def test_shortlist_empty_archive_returns_empty(session, embed_calls):
    assert shortlist_documents(session, "qualcosa") == []
