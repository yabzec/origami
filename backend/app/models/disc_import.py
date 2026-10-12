import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class DiscImportStatus(StrEnum):
    staging = "staging"
    committed = "committed"
    cancelled = "cancelled"


class DiscImport(SQLModel, table=True):
    """Files of one X-ray disc staged under tmp/disc_imports/<id>/ until commit."""

    __tablename__ = "disc_imports"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    status: str = DiscImportStatus.staging
    preview: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=utcnow)
