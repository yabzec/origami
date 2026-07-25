"""switch chunks.embedding to vector(1024) for the local bge-m3 model

Revision ID: 887ee519199f
Revises: 616180658622
Create Date: 2026-07-25 13:28:41.692756

The embedding dimension AND the vector space both change when moving from
gemini-embedding-001 (1536) to BAAI/bge-m3 (1024), so every existing embedding is
invalid — this is not optional cleanup. Derived data (chunks, summaries) is wiped and
rebuilt from the original files, which are untouched in storage. Documents are left
`pending` so the normal `process_document` worker task regenerates them; see
scripts/reingest_pending.py.

Searchable PDFs in storage are NOT invalidated — they come from Tesseract, which is
unaffected by this change.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '887ee519199f'
down_revision: Union[str, Sequence[str], None] = '616180658622'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HNSW_INDEX = (
    "CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM chunks")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1024)")
    op.execute(_HNSW_INDEX)
    op.execute(
        "UPDATE documents SET summary = NULL, status = 'pending', error_message = NULL"
    )


def downgrade() -> None:
    """Downgrade schema.

    Cannot restore the old vectors — they are deleted, and re-deriving them needs the
    cloud embedding provider this change removed. Reverts the column type only.
    """
    op.execute("DELETE FROM chunks")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1536)")
    op.execute(_HNSW_INDEX)
    op.execute(
        "UPDATE documents SET summary = NULL, status = 'pending', error_message = NULL"
    )
