"""add ocr_enabled and device

Revision ID: 616180658622
Revises: 9f9c707d06be
Create Date: 2026-07-21 09:23:29.273513

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '616180658622'
down_revision: Union[str, Sequence[str], None] = '9f9c707d06be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents",
        sa.Column("ocr_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "scan_sessions",
        sa.Column("ocr_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("scan_sessions", sa.Column("device", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("scan_sessions", "device")
    op.drop_column("scan_sessions", "ocr_enabled")
    op.drop_column("documents", "ocr_enabled")
