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
