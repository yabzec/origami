"""translation_segments for resumable page-based translation

Revision ID: a6c3e8f15d29
Revises: f2b9d4c61a87
Create Date: 2026-10-09 10:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6c3e8f15d29"
down_revision: Union[str, Sequence[str], None] = "f2b9d4c61a87"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "translation_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("source_hash", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("document_id", "segment_index"),
    )
    op.create_index("ix_translation_segments_document_id", "translation_segments", ["document_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_translation_segments_document_id", table_name="translation_segments")
    op.drop_table("translation_segments")
