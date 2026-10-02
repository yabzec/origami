"""documents.preview_path for converted office documents

Revision ID: d94a2b7e5c13
Revises: c3d81f0a6b27
Create Date: 2026-10-03 10:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d94a2b7e5c13"
down_revision: Union[str, Sequence[str], None] = "c3d81f0a6b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("preview_path", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "preview_path")
