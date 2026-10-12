from sqlalchemy import inspect, text


def test_all_tables_exist(engine):
    tables = set(inspect(engine).get_table_names())
    expected = {
        "users", "folders", "tags", "documents", "document_tags",
        "chunks", "jobs", "scan_sessions", "scan_pages",
    }
    assert expected <= tables


def test_chunks_has_vector_and_tsv(engine):
    with engine.connect() as conn:
        cols = {
            r[0]: r[1]
            for r in conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'chunks'"
            ))
        }
    assert cols["embedding"] == "USER-DEFINED"  # vector
    assert cols["content_tsv"] == "tsvector"


def test_ocr_enabled_and_device_columns(engine):
    from sqlalchemy import inspect

    inspector = inspect(engine)
    doc_cols = {c["name"] for c in inspector.get_columns("documents")}
    assert "ocr_enabled" in doc_cols
    session_cols = {c["name"] for c in inspector.get_columns("scan_sessions")}
    assert {"ocr_enabled", "device"} <= session_cols


def test_document_language_columns(engine):
    from sqlalchemy import inspect

    cols = {c["name"]: c for c in inspect(engine).get_columns("documents")}
    assert {"document_date", "detected_language", "translation_status", "ocr_applied"} <= set(cols)
    assert cols["document_date"]["nullable"] is False
    assert cols["ocr_applied"]["nullable"] is True


def test_document_preview_path_column(engine):
    cols = {c["name"]: c for c in inspect(engine).get_columns("documents")}
    assert cols["preview_path"]["nullable"] is True


def test_user_email_and_job_max_attempts_default(engine):
    inspector = inspect(engine)
    user_cols = {c["name"]: c for c in inspector.get_columns("users")}
    assert user_cols["email"]["nullable"] is True
    job_cols = {c["name"]: c for c in inspector.get_columns("jobs")}
    assert job_cols["max_attempts"]["default"] == "5"


def test_document_processing_flag_columns(engine):
    cols = {c["name"]: c for c in inspect(engine).get_columns("documents")}
    for name in ("summary_enabled", "translation_enabled"):
        assert cols[name]["nullable"] is False
        assert cols[name]["default"] == "true"


def test_translation_segments_table(engine):
    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("translation_segments")}
    assert {"document_id", "segment_index", "page_number", "source_hash", "text"} <= cols
    fks = inspector.get_foreign_keys("translation_segments")
    assert fks[0]["referred_table"] == "documents"
    assert fks[0]["options"].get("ondelete") == "CASCADE"


def test_embedding_meta_table(engine):
    from sqlalchemy import inspect

    cols = {c["name"] for c in inspect(engine).get_columns("embedding_meta")}
    assert cols == {"id", "model", "dim", "scheme", "updated_at"}


def test_embedding_column_keeps_1536_dims(engine):
    with engine.connect() as conn:
        dim = conn.execute(text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        )).scalar_one()
    assert dim == 1536


def test_disc_import_schema(engine):
    inspector = inspect(engine)
    assert {"document_attachments", "disc_imports"} <= set(inspector.get_table_names())
    doc_cols = {c["name"] for c in inspector.get_columns("documents")}
    assert {"notes", "extra_metadata"} <= doc_cols
    assert "attachment_id" in {c["name"] for c in inspector.get_columns("chunks")}


def test_attachments_and_their_chunks_cascade_with_document(session):
    from sqlmodel import select

    from app.models import Chunk, ChunkSource, DocType, Document, DocumentAttachment

    doc = Document(title="Study", doc_type=DocType.pdf, extra_metadata={"modality": "DX"})
    session.add(doc)
    session.commit()
    att = DocumentAttachment(
        document_id=doc.id, role="report", filename="Report.pdf", file_path="Study/Report.pdf", file_size=1
    )
    session.add(att)
    session.commit()
    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.content, content="x", attachment_id=att.id))
    session.commit()
    session.refresh(doc)
    assert doc.notes == ""
    assert doc.extra_metadata == {"modality": "DX"}

    session.delete(doc)
    session.commit()
    assert session.exec(select(DocumentAttachment)).all() == []
    assert session.exec(select(Chunk)).all() == []
