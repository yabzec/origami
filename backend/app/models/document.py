import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from app.config import get_default_translation_language
from app.models.user import utcnow


class DocType(StrEnum):
    scan = "scan"
    pdf = "pdf"
    text = "text"
    image = "image"
    video = "video"


class DocStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class TranslationStatus(StrEnum):
    pending = "pending"  # translate_document job queued or retrying
    done = "done"
    failed = "failed"


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str
    description: str = ""
    summary: str | None = None
    folder_id: int | None = Field(default=None, foreign_key="folders.id")
    doc_type: str  # DocType
    ocr_languages: str = "ita+eng"
    ocr_enabled: bool = True
    summary_enabled: bool = True  # False: the pipeline skips the AI summary
    translation_enabled: bool = True  # False: no translate_document job is scheduled
    translation_language: str = Field(default_factory=get_default_translation_language, max_length=8)  # ISO 639-1 target
    document_date: date = Field(default_factory=lambda: utcnow().date())
    detected_language: str | None = None  # ISO 639-1, set by local language detection
    translation_status: str | None = None  # TranslationStatus; None = not needed / not yet processed
    # True: stored PDF text layer produced by Tesseract; False: original file kept; None: unknown (legacy)
    ocr_applied: bool | None = None
    status: str = DocStatus.pending  # DocStatus
    error_message: str | None = None
    original_filename: str | None = None
    file_path: str | None = None  # relative to STORAGE_PATH
    preview_path: str | None = None  # relative PDF used only for viewing (converted office documents)
    page_count: int | None = None
    file_size: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DocumentTag(SQLModel, table=True):
    __tablename__ = "document_tags"

    document_id: uuid.UUID = Field(foreign_key="documents.id", primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)
