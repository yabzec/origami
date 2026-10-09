"""per-document translation target language

Revision ID: b1d7e4f20a93
Revises: a6c3e8f15d29
Create Date: 2026-10-09 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.config import get_primary_language


revision: str = "b1d7e4f20a93"
down_revision: Union[str, Sequence[str], None] = "a6c3e8f15d29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("translation_language", sa.String(length=8), nullable=True))
    # existing translations were made into PRIMARY_LANGUAGE: keep them valid
    op.execute(
        sa.text("UPDATE documents SET translation_language = :lang").bindparams(lang=get_primary_language())
    )
    op.alter_column("documents", "translation_language", nullable=False)


def downgrade() -> None:
    op.drop_column("documents", "translation_language")
