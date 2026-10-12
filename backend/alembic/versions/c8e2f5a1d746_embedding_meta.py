"""embedding fingerprint table

Revision ID: c8e2f5a1d746
Revises: b1d7e4f20a93
Create Date: 2026-10-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e2f5a1d746"
down_revision: Union[str, Sequence[str], None] = "b1d7e4f20a93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Filled on first start by `app.cli check-embeddings` from the current .env.
    op.create_table(
        "embedding_meta",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("scheme", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("embedding_meta")
