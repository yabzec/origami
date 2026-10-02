import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.config import get_primary_language
from app.db import get_session
from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, DocumentTag, Folder, Tag
from app.services.jobs import enqueue
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/documents", tags=["documents"], dependencies=[Depends(get_current_user)]
)


class DocumentPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    folder_id: int | None = None
    tag_ids: list[int] | None = None
    document_date: date | None = None


def doc_tags(session: Session, doc: Document) -> list[Tag]:
    return list(
        session.exec(
            select(Tag).join(DocumentTag, DocumentTag.tag_id == Tag.id)
            .where(DocumentTag.document_id == doc.id)
        )
    )


def serialize(session: Session, doc: Document) -> dict:
    return {**doc.model_dump(), "tags": [t.model_dump() for t in doc_tags(session, doc)]}


def get_doc_or_404(session: Session, document_id: uuid.UUID) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise api_error(404, "not_found", f"Document {document_id} not found")
    return doc


@router.get("")
def list_documents(
    folder_id: int | None = None,
    tag_id: int | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    query = select(Document)
    if folder_id is not None:
        query = query.where(Document.folder_id == folder_id)
    if doc_type is not None:
        query = query.where(Document.doc_type == doc_type)
    if status is not None:
        query = query.where(Document.status == status)
    if tag_id is not None:
        query = query.join(DocumentTag, DocumentTag.document_id == Document.id).where(
            DocumentTag.tag_id == tag_id
        )
    query = query.order_by(Document.created_at.desc())
    return [serialize(session, d) for d in session.exec(query)]


@router.get("/{document_id}")
def get_document(document_id: uuid.UUID, session: Session = Depends(get_session)) -> dict:
    return serialize(session, get_doc_or_404(session, document_id))


@router.get("/{document_id}/text")
def document_text(
    document_id: uuid.UUID,
    variant: Literal["content", "translation"] = "content",
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    source = ChunkSource.translation if variant == "translation" else ChunkSource.content
    chunks = session.exec(
        select(Chunk)
        .where(Chunk.document_id == doc.id, Chunk.source == source)
        .order_by(Chunk.chunk_index)
    ).all()
    return {
        "summary": doc.summary,
        "variant": variant,
        "detected_language": doc.detected_language,
        "translation_status": doc.translation_status,
        "translation_language": get_primary_language(),
        "chunks": [
            {"chunk_index": c.chunk_index, "page_number": c.page_number, "content": c.content}
            for c in chunks
        ],
    }


@router.patch("/{document_id}")
def update_document(
    document_id: uuid.UUID,
    body: DocumentPatch,
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    fields = body.model_dump(exclude_unset=True)
    tag_ids = fields.pop("tag_ids", None)

    if fields.get("document_date", ...) is None:
        fields.pop("document_date", None)  # column is NOT NULL; an empty date input means "unchanged"

    if "folder_id" in fields and fields["folder_id"] is not None:
        if session.get(Folder, fields["folder_id"]) is None:
            raise api_error(404, "not_found", "Folder not found")

    for key, value in fields.items():
        setattr(doc, key, value)

    if tag_ids is not None:
        for link in session.exec(
            select(DocumentTag).where(DocumentTag.document_id == doc.id)
        ):
            session.delete(link)
        for tag_id in tag_ids:
            if session.get(Tag, tag_id) is None:
                raise api_error(404, "not_found", f"Tag {tag_id} not found")
            session.add(DocumentTag(document_id=doc.id, tag_id=tag_id))

    doc.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(doc)
    return serialize(session, doc)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    doc = get_doc_or_404(session, document_id)
    rel_path = doc.file_path
    session.delete(doc)  # chunks and document_tags cascade via FK
    session.commit()
    storage.delete_document_file(rel_path)


class ReprocessRequest(BaseModel):
    ocr_languages: str
    ocr_enabled: bool = True


@router.post("/{document_id}/reprocess")
def reprocess_document(
    document_id: uuid.UUID,
    body: ReprocessRequest,
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    if doc.status in (DocStatus.pending, DocStatus.processing):
        raise api_error(409, "document_busy", "Document is still being processed")
    if doc.doc_type == DocType.video:
        raise api_error(422, "not_reprocessable", "Videos have no text to re-process")
    for chunk in session.exec(
        select(Chunk).where(
            Chunk.document_id == doc.id,
            Chunk.source.in_([ChunkSource.content, ChunkSource.summary, ChunkSource.translation]),
        )
    ):
        session.delete(chunk)
    doc.ocr_languages = body.ocr_languages
    doc.ocr_enabled = body.ocr_enabled
    doc.summary = None
    doc.detected_language = None
    doc.translation_status = None
    doc.error_message = None
    doc.status = DocStatus.pending
    doc.updated_at = datetime.now(timezone.utc)
    session.commit()
    enqueue(session, "process_document", {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    return serialize(session, doc)
