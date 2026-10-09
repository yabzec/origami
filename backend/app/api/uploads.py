from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, UploadFile
from sqlmodel import Session

from app.api.deps import api_error, get_current_user
from app.api.documents import serialize
from app.api.ocr import check_ocr_languages
from app.config import get_settings
from app.db import get_session
from app.models import DocType, Document, DocumentTag, Folder, Tag
from app.services.jobs import enqueue
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/documents", tags=["uploads"], dependencies=[Depends(get_current_user)]
)

EXTENSION_MAP: dict[str, DocType] = {
    ".pdf": DocType.pdf,
    ".txt": DocType.text,
    ".md": DocType.text,
    ".doc": DocType.text,
    ".docx": DocType.text,
    ".odt": DocType.text,
    ".rtf": DocType.text,
    ".png": DocType.image,
    ".jpg": DocType.image,
    ".jpeg": DocType.image,
    ".tif": DocType.image,
    ".tiff": DocType.image,
    ".webp": DocType.image,
    ".mp4": DocType.video,
    ".mkv": DocType.video,
    ".mov": DocType.video,
    ".avi": DocType.video,
    ".webm": DocType.video,
}


def create_pending_document(
    session: Session,
    *,
    title: str,
    doc_type: str,
    ocr_languages: str,
    ocr_enabled: bool = True,
    summary_enabled: bool = True,
    translation_enabled: bool = True,
    folder_id: int | None,
    tag_ids: list[int],
    original_filename: str | None,
    description: str = "",
    document_date: date | None = None,
) -> Document:
    """Insert a pending document with validated folder/tags. Reused by scan compile."""
    if folder_id is not None and session.get(Folder, folder_id) is None:
        raise api_error(404, "not_found", "Folder not found")
    for tag_id in tag_ids:
        if session.get(Tag, tag_id) is None:
            raise api_error(404, "not_found", f"Tag {tag_id} not found")
    doc = Document(
        title=title,
        description=description,
        doc_type=doc_type,
        ocr_languages=ocr_languages,
        ocr_enabled=ocr_enabled,
        summary_enabled=summary_enabled,
        translation_enabled=translation_enabled,
        folder_id=folder_id,
        original_filename=original_filename,
    )
    if document_date is not None:
        doc.document_date = document_date
    session.add(doc)
    session.commit()
    session.refresh(doc)
    for tag_id in tag_ids:
        session.add(DocumentTag(document_id=doc.id, tag_id=tag_id))
    session.commit()
    return doc


def parse_tag_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise api_error(422, "validation_error", "tag_ids must be comma-separated integers")


@router.post("/upload", status_code=201)
def upload_document(
    file: UploadFile,
    title: str | None = Form(default=None),
    folder_id: int | None = Form(default=None),
    tag_ids: str | None = Form(default=None),
    ocr_languages: str | None = Form(default=None),
    ocr_enabled: bool = Form(default=True),
    summary_enabled: bool = Form(default=True),
    translation_enabled: bool = Form(default=True),
    document_date: date | None = Form(default=None),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    check_ocr_languages(ocr_languages)
    ext = Path(file.filename or "").suffix.lower()
    doc_type = EXTENSION_MAP.get(ext)
    if doc_type is None:
        raise api_error(422, "unsupported_type", f"Unsupported file extension {ext!r}")

    doc = create_pending_document(
        session,
        title=title or Path(file.filename).stem,
        doc_type=doc_type,
        ocr_languages=ocr_languages or get_settings().default_ocr_languages,
        ocr_enabled=ocr_enabled,
        summary_enabled=summary_enabled,
        translation_enabled=translation_enabled,
        folder_id=folder_id,
        tag_ids=parse_tag_ids(tag_ids),
        original_filename=file.filename,
        document_date=document_date,
    )
    rel, size = storage.store_fileobj(doc.id, ext, file.file)
    doc.file_path = rel
    doc.file_size = size
    session.commit()

    enqueue(session, "process_document", {"document_id": str(doc.id)})
    session.refresh(doc)
    return serialize(session, doc)
