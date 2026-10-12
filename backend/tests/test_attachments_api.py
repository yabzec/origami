from app.models import DocType, Document, DocumentAttachment
from app.services import rag
from app.services.auth import create_access_token
from app.services.search import search
from tests.helpers import basis_vector, seed_document


def make_doc_with_report(session, storage):
    doc = Document(title="Study", doc_type=DocType.pdf, file_path="Study/Study.pdf", extra_metadata={})
    session.add(doc)
    session.commit()
    storage.write_file("Study/Study.pdf", b"%PDF images")
    storage.write_file("Study/Report.pdf", b"%PDF report")
    att = DocumentAttachment(
        document_id=doc.id, role="report", filename="Report.pdf", file_path="Study/Report.pdf",
        file_size=11, page_count=1,
    )
    session.add(att)
    session.commit()
    session.refresh(doc)
    session.refresh(att)
    return doc, att


def test_document_json_lists_attachments(auth_client, session, storage):
    doc, att = make_doc_with_report(session, storage)
    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert body["attachments"] == [
        {"id": att.id, "role": "report", "filename": "Report.pdf", "file_size": 11, "page_count": 1}
    ]
    listed = auth_client.get("/api/documents").json()
    assert listed[0]["attachments"][0]["id"] == att.id


def test_attachment_file_inline_and_download(auth_client, session, storage):
    doc, att = make_doc_with_report(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/attachments/{att.id}/file")
    assert resp.status_code == 200
    assert resp.content == b"%PDF report"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")
    resp = auth_client.get(f"/api/documents/{doc.id}/attachments/{att.id}/file?download=1")
    assert resp.headers["content-disposition"].startswith("attachment")


def test_attachment_file_with_token_query(client, user, session, storage):
    doc, att = make_doc_with_report(session, storage)
    token = create_access_token(user.id)
    resp = client.get(f"/api/documents/{doc.id}/attachments/{att.id}/file?token={token}")
    assert resp.status_code == 200


def test_attachment_of_other_document_is_404(auth_client, session, storage):
    doc, att = make_doc_with_report(session, storage)
    other = Document(title="Other", doc_type=DocType.pdf)
    session.add(other)
    session.commit()
    resp = auth_client.get(f"/api/documents/{other.id}/attachments/{att.id}/file")
    assert resp.status_code == 404


def test_search_snippet_carries_attachment_id(session, storage):
    doc, att = make_doc_with_report(session, storage)
    seed_document(session, "Unrelated", [{"content": "bolletta della luce"}])
    from app.models import Chunk, ChunkSource

    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.content,
                      content="Frattura saldata della tibia destra.", attachment_id=att.id))
    session.commit()
    [result] = search(session, "tibia", mode="keyword")
    assert result["snippets"][0]["attachment_id"] == att.id


def test_retrieve_carries_attachment_id(session, storage, monkeypatch):
    doc, att = make_doc_with_report(session, storage)
    from app.models import Chunk, ChunkSource

    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.content, content="tibia",
                      attachment_id=att.id, embedding=basis_vector(0)))
    session.commit()
    monkeypatch.setattr(rag, "llm_embed", lambda texts, role="document": [basis_vector(0)])
    sources, _ = rag.retrieve(session, "tibia", [doc.id])
    assert sources[0].attachment_id == att.id
