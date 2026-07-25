import uuid
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

EMBEDDING_DIM = 1024  # BAAI/bge-m3 dense output; must match the migrated column type


class ChunkSource(StrEnum):
    content = "content"
    summary = "summary"
    metadata = "metadata"


class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", index=True)
    chunk_index: int
    page_number: int | None = None
    source: str = ChunkSource.content  # ChunkSource
    content: str
    embedding: Any = Field(default=None, sa_column=Column(Vector(EMBEDDING_DIM)))
