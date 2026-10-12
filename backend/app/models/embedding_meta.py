from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class EmbeddingMeta(SQLModel, table=True):
    """Fingerprint of the vectors in chunks.embedding (one row, id 1)."""

    __tablename__ = "embedding_meta"

    id: int = Field(default=1, primary_key=True)
    model: str
    dim: int
    scheme: str
    updated_at: datetime = Field(default_factory=utcnow)
