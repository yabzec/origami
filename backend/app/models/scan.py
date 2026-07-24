from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class ScanSessionStatus(StrEnum):
    active = "active"
    compiling = "compiling"
    done = "done"
    cancelled = "cancelled"


class ScanSession(SQLModel, table=True):
    __tablename__ = "scan_sessions"

    id: int | None = Field(default=None, primary_key=True)
    status: str = ScanSessionStatus.active
    ocr_languages: str = "ita+eng"
    ocr_enabled: bool = True
    device: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ScanPage(SQLModel, table=True):
    __tablename__ = "scan_pages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="scan_sessions.id", index=True)
    page_number: int
    image_path: str
