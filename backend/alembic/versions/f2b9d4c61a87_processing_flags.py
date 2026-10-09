"""documents.summary_enabled / translation_enabled

Revision ID: f2b9d4c61a87
Revises: e5a1c7d93b20
Create Date: 2026-10-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2b9d4c61a87"
down_revision: Union[str, Sequence[str], None] = "e5a1c7d93b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents", sa.Column("summary_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "documents", sa.Column("translation_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "translation_enabled")
    op.drop_column("documents", "summary_enabled")
