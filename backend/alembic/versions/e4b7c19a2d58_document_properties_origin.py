"""document properties (ordered key/value list) and origin

Revision ID: e4b7c19a2d58
Revises: a3f9c2d71b48
Create Date: 2026-10-12 21:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e4b7c19a2d58"
down_revision: Union[str, Sequence[str], None] = "a3f9c2d71b48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Copy of app.services.study_metadata.FIELDS at the time of this migration (no app imports here).
LABELS = [
    ("patient_name", "Patient"),
    ("birth_date", "Birth date"),
    ("sex", "Sex"),
    ("patient_id", "Patient ID"),
    ("study_description", "Study"),
    ("modality", "Modality"),
    ("body_part", "Body part"),
    ("institution", "Institution"),
    ("accession_number", "Accession number"),
    ("device", "Device"),
]

documents = sa.table(
    "documents",
    sa.column("id", sa.Uuid()),
    sa.column("origin", sa.String()),
    sa.column("properties", postgresql.JSONB()),
    sa.column("extra_metadata", postgresql.JSONB()),
)


def _as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _to_rows(meta: dict) -> list[dict]:
    rows = [{"key": label, "value": _as_text(meta[key])} for key, label in LABELS if meta.get(key)]
    series = meta.get("series") or []
    if series:
        rows.append(
            {"key": "Series", "value": "; ".join(f"{s['description']} ({s['images']})" for s in series)}
        )
    known = {key for key, _ in LABELS} | {"series"}
    rows += [{"key": key, "value": _as_text(value)} for key, value in meta.items() if key not in known and value]
    return rows


def upgrade() -> None:
    op.add_column("documents", sa.Column("origin", sa.String(), nullable=False, server_default="file"))
    op.execute("UPDATE documents SET origin = 'scan' WHERE doc_type = 'scan'")
    op.execute("UPDATE documents SET origin = 'xray_disc' WHERE extra_metadata IS NOT NULL")
    op.alter_column("documents", "extra_metadata", new_column_name="properties")
    conn = op.get_bind()
    for doc_id, meta in conn.execute(
        sa.select(documents.c.id, documents.c.properties).where(documents.c.properties.is_not(None))
    ).all():
        rows = _to_rows(meta) if isinstance(meta, dict) else []
        conn.execute(documents.update().where(documents.c.id == doc_id).values(properties=rows))
    op.execute("UPDATE documents SET properties = '[]'::jsonb WHERE properties IS NULL")
    op.alter_column(
        "documents", "properties", nullable=False, server_default=sa.text("'[]'::jsonb")
    )


def downgrade() -> None:
    op.alter_column(
        "documents", "properties", new_column_name="extra_metadata", nullable=True, server_default=None
    )
    conn = op.get_bind()
    # best effort: X-ray studies get a dict keyed by label; other documents lose their properties
    for doc_id, origin, rows in conn.execute(
        sa.select(documents.c.id, documents.c.origin, documents.c.extra_metadata)
    ).all():
        meta = {row["key"]: row["value"] for row in rows or []} if origin == "xray_disc" else None
        conn.execute(
            documents.update().where(documents.c.id == doc_id).values(
                extra_metadata=sa.null() if meta is None else meta
            )
        )
    op.drop_column("documents", "origin")
