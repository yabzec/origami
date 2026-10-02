from datetime import date

from alembic import command
from alembic.config import Config
from sqlalchemy import text

TEST_URL = "postgresql+psycopg://origami:origami@localhost:5432/origami_test"
PREVIOUS = "616180658622"


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    return cfg


def test_document_date_backfilled_from_created_at(engine):
    cfg = _cfg()
    command.downgrade(cfg, PREVIOUS)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO documents (id, title, description, doc_type, ocr_languages, "
                "ocr_enabled, status, created_at, updated_at) VALUES (gen_random_uuid(), "
                "'MigrationOld', '', 'pdf', 'ita', true, 'ready', "
                "'2020-05-17 10:00:00', '2020-05-17 10:00:00')"
            ))
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            value = conn.execute(
                text("SELECT document_date FROM documents WHERE title = 'MigrationOld'")
            ).scalar_one()
        assert value == date(2020, 5, 17)
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title = 'MigrationOld'"))
