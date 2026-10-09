import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.api.ocr import check_ocr_languages
from app.config import get_primary_language
from app.db import get_session
from app.models import (
    Chunk,
    ChunkSource,
    DocStatus,
    DocType,
    Document,
    DocumentTag,
    Folder,
    Job,
    JobStatus,
    ScanPage,
    Tag,
    TranslationStatus,
)
from app.services.storage import Storage, get_storage
from app.worker.pipeline import delete_translation_segments

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


DOCUMENT_JOB_TYPES = ("process_document", "translate_document")


def _utc_iso(value: datetime) -> str:
    # job timestamps are stored naive in UTC; an explicit offset stops browsers reading local time
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def active_jobs_for(session: Session, doc_ids: list[uuid.UUID]) -> dict[str, dict]:
    """Newest queued/running job per document, fetched in one query."""
    if not doc_ids:
        return {}
    jobs = session.exec(
        select(Job)
        .where(
            Job.status.in_([JobStatus.queued, JobStatus.running]),
            Job.payload["document_id"].astext.in_([str(i) for i in doc_ids]),
        )
        .order_by(Job.id.desc())
    ).all()
    active: dict[str, dict] = {}
    for job in jobs:
        active.setdefault(
            job.payload["document_id"],
            {
                "type": job.type,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "run_at": _utc_iso(job.run_at),
                "last_error": job.last_error,
            },
        )
    return active


def serialize(session: Session, doc: Document, active_jobs: dict[str, dict] | None = None) -> dict:
    if active_jobs is None:
        active_jobs = active_jobs_for(session, [doc.id])
    return {
        **doc.model_dump(),
        "translatable": bool(doc.detected_language) and doc.detected_language != get_primary_language(),
        "tags": [t.model_dump() for t in doc_tags(session, doc)],
        "active_job": active_jobs.get(str(doc.id)),
    }


def get_doc_or_404(session: Session, document_id: uuid.UUID) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise api_error(404, "not_found", f"Document {document_id} not found")
    return doc


DocumentSort = Literal["date_desc", "date_asc", "added_desc", "title_asc"]

SORT_ORDER = {
    "date_desc": (Document.document_date.desc(), Document.created_at.desc()),
    "date_asc": (Document.document_date.asc(), Document.created_at.asc()),
    "added_desc": (Document.created_at.desc(),),
    "title_asc": (func.lower(Document.title).asc(), Document.created_at.desc()),
}


@router.get("")
def list_documents(
    folder_id: str | None = None,
    tag_id: int | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: DocumentSort = "date_desc",
    session: Session = Depends(get_session),
) -> list[dict]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise api_error(422, "invalid_date_range", "date_from must not be after date_to")
    query = select(Document)
    if folder_id == "root":
        query = query.where(Document.folder_id.is_(None))
    elif folder_id is not None:
        if not folder_id.isdigit():
            raise api_error(422, "validation_error", "folder_id must be an integer or 'root'")
        query = query.where(Document.folder_id == int(folder_id))
    if date_from is not None:
        query = query.where(Document.document_date >= date_from)
    if date_to is not None:
        query = query.where(Document.document_date <= date_to)
    if doc_type is not None:
        query = query.where(Document.doc_type == doc_type)
    if status is not None:
        query = query.where(Document.status == status)
    if tag_id is not None:
        query = query.join(DocumentTag, DocumentTag.document_id == Document.id).where(
            DocumentTag.tag_id == tag_id
        )
    query = query.order_by(*SORT_ORDER[sort])
    docs = list(session.exec(query))
    active_jobs = active_jobs_for(session, [d.id for d in docs])
    return [serialize(session, d, active_jobs) for d in docs]


BULK_MAX = 500


class BulkIds(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=BULK_MAX)


class BulkMove(BulkIds):
    folder_id: int | None


def _found_and_missing(session: Session, ids: list[uuid.UUID]) -> tuple[list[Document], list[str]]:
    docs = list(session.exec(select(Document).where(Document.id.in_(ids))))
    found = {d.id for d in docs}
    return docs, [str(i) for i in ids if i not in found]


def _delete_documents(session: Session, docs: list[Document]) -> list[str | None]:
    """Cancel queued jobs and delete rows (no commit); returns the file paths to remove after commit."""
    rel_paths: list[str | None] = []
    for doc in docs:
        rel_paths += [doc.file_path, doc.preview_path]
        _cancel_queued_jobs(session, doc)
        session.delete(doc)  # chunks, document_tags and translation segments cascade via FK
    return rel_paths


@router.post("/bulk/move")
def bulk_move(body: BulkMove, session: Session = Depends(get_session)) -> dict:
    if body.folder_id is not None and session.get(Folder, body.folder_id) is None:
        raise api_error(404, "not_found", "Folder not found")
    docs, missing = _found_and_missing(session, body.ids)
    now = datetime.now(timezone.utc)
    for doc in docs:
        doc.folder_id = body.folder_id
        doc.updated_at = now
    session.commit()
    return {"moved": len(docs), "missing": missing}


@router.post("/bulk/delete")
def bulk_delete(
    body: BulkIds,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    docs, missing = _found_and_missing(session, body.ids)
    rel_paths = _delete_documents(session, docs)
    session.commit()
    for rel in rel_paths:
        storage.delete_document_file(rel)
    return {"deleted": len(docs), "missing": missing}


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
    rel_paths = _delete_documents(session, [doc])
    session.commit()
    for rel in rel_paths:
        storage.delete_document_file(rel)


class ReprocessRequest(BaseModel):
    ocr_languages: str
    ocr_enabled: bool = True
    summary_enabled: bool = True
    translation_enabled: bool = True


@router.post("/{document_id}/reprocess")
def reprocess_document(
    document_id: uuid.UUID,
    body: ReprocessRequest,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    check_ocr_languages(body.ocr_languages)
    if doc.status in (DocStatus.pending, DocStatus.processing):
        raise api_error(409, "document_busy", "Document is still being processed")
    if doc.doc_type == DocType.video:
        raise api_error(422, "not_reprocessable", "Videos have no text to re-process")
    payload: dict = {"document_id": str(doc.id), "force_ocr": True}
    if doc.doc_type == DocType.scan and not (
        doc.file_path and storage.abs_path(doc.file_path).exists()
    ):
        # First compile failed before the PDF was stored: rebuild from the scan page images.
        scan_session_id = _scan_session_with_pages(session, storage, doc)
        if scan_session_id is None:
            raise api_error(409, "no_source", "The original scanned pages are no longer available")
        payload["scan_session_id"] = scan_session_id
    session.refresh(doc, with_for_update=True)  # serialize with translate_document's writes
    if doc.status in (DocStatus.pending, DocStatus.processing):
        raise api_error(409, "document_busy", "Document is still being processed")
    _cancel_queued_jobs(session, doc)
    for chunk in session.exec(
        select(Chunk).where(
            Chunk.document_id == doc.id,
            Chunk.source.in_(
                [ChunkSource.content, ChunkSource.summary, ChunkSource.translation, ChunkSource.metadata]
            ),
        )
    ):
        session.delete(chunk)
    delete_translation_segments(session, doc.id)
    doc.ocr_languages = body.ocr_languages
    doc.ocr_enabled = body.ocr_enabled
    doc.summary_enabled = body.summary_enabled
    doc.translation_enabled = body.translation_enabled
    if doc.summary and doc.description == doc.summary:
        doc.description = ""  # still the AI text: the new summary refills it; edited text is kept
    doc.summary = None
    doc.detected_language = None
    doc.translation_status = None
    doc.error_message = None
    doc.status = DocStatus.pending
    doc.updated_at = datetime.now(timezone.utc)
    # same row enqueue() would create, committed together with the reset (atomic)
    session.add(Job(type="process_document", payload=payload, run_at=datetime.now(timezone.utc)))
    session.commit()
    session.refresh(doc)
    return serialize(session, doc)


@router.post("/{document_id}/retranslate")
def retranslate_document(document_id: uuid.UUID, session: Session = Depends(get_session)) -> dict:
    doc = get_doc_or_404(session, document_id)
    session.refresh(doc, with_for_update=True)  # same lock as reprocess and translate_document
    if doc.status in (DocStatus.pending, DocStatus.processing):
        raise api_error(409, "document_busy", "Document is still being processed")
    if doc.translation_status == TranslationStatus.pending:
        raise api_error(409, "translation_busy", "A translation is already in progress")
    if not doc.detected_language or doc.detected_language == get_primary_language():
        raise api_error(409, "nothing_to_translate", "The document is already in the primary language")
    _cancel_queued_jobs(session, doc)
    for chunk in session.exec(
        select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.translation)
    ):
        session.delete(chunk)
    delete_translation_segments(session, doc.id)
    doc.translation_enabled = True
    doc.translation_status = TranslationStatus.pending
    doc.updated_at = datetime.now(timezone.utc)
    session.add(
        Job(type="translate_document", payload={"document_id": str(doc.id)}, run_at=datetime.now(timezone.utc))
    )
    session.commit()
    session.refresh(doc)
    return serialize(session, doc)


def _cancel_queued_jobs(session: Session, doc: Document) -> None:
    """Re-process supersedes queued work; a running job finishes and its results get replaced."""
    for job in session.exec(
        select(Job).where(
            Job.type.in_(DOCUMENT_JOB_TYPES),
            Job.status == JobStatus.queued,
            Job.payload["document_id"].astext == str(doc.id),
        )
    ):
        job.status = JobStatus.cancelled
        job.updated_at = datetime.now(timezone.utc)


def _scan_session_with_pages(session: Session, storage: Storage, doc: Document) -> int | None:
    """Scan session of the doc's latest compile job, if its page images still exist on disk."""
    jobs = session.exec(
        select(Job)
        .where(Job.type == "process_document", Job.payload["document_id"].astext == str(doc.id))
        .order_by(Job.id.desc())
    ).all()
    scan_session_id = next(
        (j.payload["scan_session_id"] for j in jobs if j.payload.get("scan_session_id") is not None),
        None,
    )
    if scan_session_id is None:
        return None
    pages = session.exec(select(ScanPage).where(ScanPage.session_id == scan_session_id)).all()
    if not pages or not all(storage.abs_path(p.image_path).exists() for p in pages):
        return None
    return scan_session_id
