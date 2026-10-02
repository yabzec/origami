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
