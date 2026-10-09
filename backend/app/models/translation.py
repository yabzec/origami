import uuid

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TranslationSegment(SQLModel, table=True):
    """A translated source segment (page or part of a page), kept until the translation completes."""

    __tablename__ = "translation_segments"
    __table_args__ = (UniqueConstraint("document_id", "segment_index"),)

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", index=True)
    segment_index: int
    page_number: int | None = None
    source_hash: str  # sha256 hex of the source text; a mismatch means the source changed
    text: str
