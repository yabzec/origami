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


BEFORE_BACKFILL = "b7c4e2a91d05"
DESCRIPTION_BACKFILL = "c3d81f0a6b27"


def _backfill_descriptions(engine) -> dict[str, str]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT title, description FROM documents WHERE title LIKE 'Backfill%'")
        ).all()
    return {title: description for title, description in rows}


def test_summary_backfills_empty_description(engine):
    cfg = _cfg()
    command.downgrade(cfg, BEFORE_BACKFILL)
    try:
        with engine.begin() as conn:
            for title, description, summary in [
                ("BackfillEmpty", "", "Riassunto A."),
                ("BackfillBlank", "   ", "Riassunto B."),
                ("BackfillUser", "Mia nota", "Riassunto C."),
                ("BackfillNoSummary", "", None),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO documents (id, title, description, summary, doc_type, "
                        "ocr_languages, ocr_enabled, status, created_at, updated_at) VALUES "
                        "(gen_random_uuid(), :title, :description, :summary, 'pdf', 'ita', true, "
                        "'ready', now(), now())"
                    ),
                    {"title": title, "description": description, "summary": summary},
                )
        command.upgrade(cfg, DESCRIPTION_BACKFILL)
        assert _backfill_descriptions(engine) == {
            "BackfillEmpty": "Riassunto A.",
            "BackfillBlank": "Riassunto B.",
            "BackfillUser": "Mia nota",
            "BackfillNoSummary": "",
        }
        command.downgrade(cfg, BEFORE_BACKFILL)
        assert _backfill_descriptions(engine) == {
            "BackfillEmpty": "",
            "BackfillBlank": "",
            "BackfillUser": "Mia nota",
            "BackfillNoSummary": "",
        }
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title LIKE 'Backfill%'"))


RETRY_REVISION = "e5a1c7d93b20"


def test_retry_migration_raises_max_attempts_of_open_jobs_only(engine):
    from alembic.script import ScriptDirectory

    cfg = _cfg()
    previous = ScriptDirectory.from_config(cfg).get_revision(RETRY_REVISION).down_revision
    command.downgrade(cfg, previous)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO jobs (type, payload, status, attempts, max_attempts, run_at, "
                "created_at, updated_at) VALUES "
                "('mig_queued', '{}', 'queued', 1, 3, now(), now(), now()), "
                "('mig_running', '{}', 'running', 0, 3, now(), now(), now()), "
                "('mig_done', '{}', 'done', 1, 3, now(), now(), now())"
            ))
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            rows = dict(conn.execute(
                text("SELECT type, max_attempts FROM jobs WHERE type LIKE 'mig_%'")
            ).all())
        assert rows == {"mig_queued": 5, "mig_running": 5, "mig_done": 3}
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM jobs WHERE type LIKE 'mig_%'"))


def test_translation_language_backfilled(engine):
    cfg = _cfg()
    command.downgrade(cfg, "a6c3e8f15d29")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO documents (id, title, description, doc_type, ocr_languages, ocr_enabled, "
                "summary_enabled, translation_enabled, document_date, status, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'Backfill', '', 'pdf', 'ita', true, true, true, "
                "'2026-01-01', 'ready', now(), now())"
            ))
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            value = conn.execute(
                text("SELECT translation_language FROM documents WHERE title = 'Backfill'")
            ).scalar_one()
        assert value == "it"
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title = 'Backfill'"))


BEFORE_PROPERTIES = "a3f9c2d71b48"


def test_properties_and_origin_backfilled(engine):
    cfg = _cfg()
    command.downgrade(cfg, BEFORE_PROPERTIES)
    try:
        with engine.begin() as conn:
            insert = text(
                "INSERT INTO documents (id, title, description, notes, doc_type, ocr_languages, ocr_enabled, "
                "summary_enabled, translation_enabled, translation_language, document_date, status, "
                "extra_metadata, created_at, updated_at) VALUES (gen_random_uuid(), :title, '', '', :doc_type, "
                "'ita', true, true, false, 'it', '2026-01-01', 'ready', CAST(:meta AS jsonb), now(), now())"
            )
            meta = (
                '{"modality": "DX", "patient_name": "Mario Rossi", "sex": "", "custom": "x", '
                '"series": [{"description": "AP", "images": 2}, {"description": "Lateral", "images": 2}]}'
            )
            conn.execute(insert, {"title": "PropStudy", "doc_type": "pdf", "meta": meta})
            conn.execute(insert, {"title": "PropScan", "doc_type": "scan", "meta": None})
            conn.execute(insert, {"title": "PropFile", "doc_type": "pdf", "meta": None})
            # the old ORM stored Python None as JSON null
            conn.execute(insert, {"title": "PropJsonNullFile", "doc_type": "pdf", "meta": "null"})
            conn.execute(insert, {"title": "PropJsonNullScan", "doc_type": "scan", "meta": "null"})
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            rows = {
                title: (origin, props)
                for title, origin, props in conn.execute(
                    text("SELECT title, origin, properties FROM documents WHERE title LIKE 'Prop%'")
                ).all()
            }
        assert rows["PropStudy"] == (
            "xray_disc",
            [
                {"key": "Patient", "value": "Mario Rossi"},
                {"key": "Modality", "value": "DX"},
                {"key": "Series", "value": "AP (2); Lateral (2)"},
                {"key": "custom", "value": "x"},
            ],
        )
        assert rows["PropScan"] == ("scan", [])
        assert rows["PropFile"] == ("file", [])
        assert rows["PropJsonNullFile"] == ("file", [])
        assert rows["PropJsonNullScan"] == ("scan", [])
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title LIKE 'Prop%'"))
