"""document date, detected language, translation status

Revision ID: b7c4e2a91d05
Revises: 616180658622
Create Date: 2026-10-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c4e2a91d05"
down_revision: Union[str, Sequence[str], None] = "616180658622"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("document_date", sa.Date(), nullable=True))
    op.execute("UPDATE documents SET document_date = created_at::date")
    op.alter_column(
        "documents",
        "document_date",
        nullable=False,
        server_default=sa.text("CURRENT_DATE"),
    )
    op.add_column("documents", sa.Column("detected_language", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("translation_status", sa.String(), nullable=True))
    # NULL for existing rows: provenance of their stored PDF is unknown
    op.add_column("documents", sa.Column("ocr_applied", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "ocr_applied")
    op.drop_column("documents", "translation_status")
    op.drop_column("documents", "detected_language")
    op.drop_column("documents", "document_date")
