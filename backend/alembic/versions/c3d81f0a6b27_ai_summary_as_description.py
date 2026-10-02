"""AI summary becomes the description (data only)

Revision ID: c3d81f0a6b27
Revises: b7c4e2a91d05
Create Date: 2026-10-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c3d81f0a6b27"
down_revision: Union[str, Sequence[str], None] = "b7c4e2a91d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Copy the AI summary into empty descriptions."""
    op.execute(
        "UPDATE documents SET description = summary "
        "WHERE (description IS NULL OR btrim(description) = '') "
        "AND summary IS NOT NULL AND btrim(summary) <> ''"
    )


def downgrade() -> None:
    """Clear descriptions that are still the AI summary."""
    op.execute("UPDATE documents SET description = '' WHERE description = summary")
