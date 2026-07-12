from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class Folder(SQLModel, table=True):
    __tablename__ = "folders"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    parent_id: int | None = Field(default=None, foreign_key="folders.id")
    created_at: datetime = Field(default_factory=utcnow)
