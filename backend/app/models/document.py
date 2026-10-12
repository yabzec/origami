import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Column, text
from sqlalchemy.dialects.postgresql import JSONB
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


class DocumentOrigin(StrEnum):
    file = "file"  # uploaded file
    scan = "scan"  # compiled from a scan session
    xray_disc = "xray_disc"  # imported X-ray disc study


class TranslationStatus(StrEnum):
    pending = "pending"  # translate_document job queued or retrying
    done = "done"
    failed = "failed"


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str
    description: str = ""
    notes: str = ""  # free text from the user; part of the metadata chunk
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
    origin: str = DocumentOrigin.file  # DocumentOrigin
    # ordered key/value rows ({"key", "value"}), editable on every document; searchable
    properties: list[dict[str, str]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DocumentTag(SQLModel, table=True):
    __tablename__ = "document_tags"

    document_id: uuid.UUID = Field(foreign_key="documents.id", primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)


class AttachmentRole(StrEnum):
    report = "report"


class DocumentAttachment(SQLModel, table=True):
    """Extra file stored next to the main file, inside the document's bundle directory."""

    __tablename__ = "document_attachments"

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", index=True)
    role: str  # AttachmentRole
    filename: str  # name inside the bundle directory
    file_path: str  # relative to STORAGE_PATH
    file_size: int
    page_count: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
