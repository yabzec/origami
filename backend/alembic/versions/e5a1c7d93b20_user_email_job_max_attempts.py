"""user email, five job attempts

Revision ID: e5a1c7d93b20
Revises: d94a2b7e5c13
Create Date: 2026-10-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a1c7d93b20"
down_revision: Union[str, Sequence[str], None] = "d94a2b7e5c13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.alter_column("jobs", "max_attempts", server_default=sa.text("5"))
    # open jobs get the new retry budget; finished ones keep their history
    op.execute("UPDATE jobs SET max_attempts = 5 WHERE status IN ('queued', 'running')")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("jobs", "max_attempts", server_default=None)
    op.drop_column("users", "email")
