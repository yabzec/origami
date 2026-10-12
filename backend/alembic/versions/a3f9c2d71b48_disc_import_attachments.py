"""document notes, study metadata, attachments and disc import staging

Revision ID: a3f9c2d71b48
Revises: c8e2f5a1d746
Create Date: 2026-10-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a3f9c2d71b48"
down_revision: Union[str, Sequence[str], None] = "c8e2f5a1d746"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("notes", sa.String(), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("extra_metadata", postgresql.JSONB(), nullable=True))
    op.create_table(
        "document_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_document_attachments_document_id", "document_attachments", ["document_id"])
    op.add_column(
        "chunks",
        sa.Column(
            "attachment_id",
            sa.Integer(),
            sa.ForeignKey("document_attachments.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_chunks_attachment_id", "chunks", ["attachment_id"])
    op.create_table(
        "disc_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("preview", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("disc_imports")
    op.drop_index("ix_chunks_attachment_id", table_name="chunks")
    op.drop_column("chunks", "attachment_id")
    op.drop_index("ix_document_attachments_document_id", table_name="document_attachments")
    op.drop_table("document_attachments")
    op.drop_column("documents", "extra_metadata")
    op.drop_column("documents", "notes")
