import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Chunk, ChunkSource, DocStatus, DocType, Document
from app.services.chunking import chunk_pages
from app.services.extract import (
    extract_docx,
    extract_pdf_text,
    extract_text_file,
    pdf_needs_ocr,
)
from app.services.llm import describe as llm_describe
from app.services.llm import embed as llm_embed
from app.services.ocr import images_to_searchable_pdf, ocr_image, pdf_to_searchable_pdf
from app.services.storage import Storage
from app.worker.runner import register

log = logging.getLogger("origami.pipeline")

SUMMARY_INPUT_CHARS = 8000


def get_pipeline_storage() -> Storage:
    """Worker-side storage factory (no FastAPI DI in the worker process)."""
    return Storage(get_settings().storage_path)


@register("process_document")
def process_document(session: Session, payload: dict) -> None:
    doc = session.get(Document, payload["document_id"])
    if doc is None:
        raise ValueError(f"Document {payload['document_id']} not found")
    storage = get_pipeline_storage()
    try:
        doc.status = DocStatus.processing
        session.commit()

        pages = _extract_content(session, doc, storage, payload)
        _ensure_content_chunks(session, doc, pages)
        _ensure_summary(session, doc, storage)
        _ensure_metadata_chunk(session, doc)
        _embed_pending_chunks(session, doc)

        doc.status = DocStatus.ready
        doc.error_message = None
        session.commit()
    except Exception as exc:
        session.rollback()
        doc.status = DocStatus.failed
        doc.error_message = str(exc)[:2000]
        session.commit()
        raise


def _has_chunks(session: Session, doc: Document, source: str) -> bool:
    return (
        session.exec(
            select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == source)
        ).first()
        is not None
    )


def _extract_content(
    session: Session, doc: Document, storage: Storage, payload: dict
) -> list[tuple[int | None, str]]:
    """Return page texts; skip (return []) if content chunks already exist."""
    if doc.doc_type == DocType.video or _has_chunks(session, doc, ChunkSource.content):
        return []

    if doc.doc_type == DocType.scan:
        return _extract_scan(session, doc, storage, payload)  # Task 9

    path = storage.abs_path(doc.file_path)

    if doc.doc_type == DocType.text:
        text = extract_docx(path) if path.suffix == ".docx" else extract_text_file(path)
        return [(None, text)]

    if doc.doc_type == DocType.image:
        pdf_bytes, text = ocr_image(path, doc.ocr_languages)
        # companion searchable PDF alongside the original image
        storage.store_file(doc.id, ".pdf", pdf_bytes)
        return [(1, text)]

    if doc.doc_type == DocType.pdf:
        pages = extract_pdf_text(path)
        if pdf_needs_ocr(pages):
            pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
            rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
            doc.file_path = rel
            doc.file_size = size
        doc.page_count = len(pages)
        session.commit()
        return pages

    raise ValueError(f"Unknown doc_type {doc.doc_type!r}")


def _extract_scan(
    session: Session, doc: Document, storage: Storage, payload: dict
) -> list[tuple[int | None, str]]:
    from app.models import ScanPage, ScanSession, ScanSessionStatus

    session_id = payload["scan_session_id"]
    if doc.file_path and storage.abs_path(doc.file_path).exists():
        # retry after the PDF was already built: recover text from the stored PDF
        pages = extract_pdf_text(storage.abs_path(doc.file_path))
    else:
        page_rows = session.exec(
            select(ScanPage)
            .where(ScanPage.session_id == session_id)
            .order_by(ScanPage.page_number)
        ).all()
        image_paths = [storage.abs_path(p.image_path) for p in page_rows]
        pdf_bytes, pages = images_to_searchable_pdf(image_paths, doc.ocr_languages)
        rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
        doc.file_path = rel
        doc.file_size = size
    doc.page_count = len(pages)
    scan_session = session.get(ScanSession, session_id)
    if scan_session is not None:
        scan_session.status = ScanSessionStatus.done
    session.commit()
    storage.remove_scan_session_dir(session_id)
    return pages


def _ensure_content_chunks(
    session: Session, doc: Document, pages: list[tuple[int | None, str]]
) -> None:
    if not pages or _has_chunks(session, doc, ChunkSource.content):
        return
    next_index = 0
    for chunk in chunk_pages(pages):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=next_index,
                page_number=chunk["page_number"],
                source=ChunkSource.content,
                content=chunk["content"],
            )
        )
        next_index += 1
    session.commit()


def _ensure_summary(session: Session, doc: Document, storage: Storage) -> None:
    if doc.doc_type in (DocType.video, DocType.scan) or doc.summary is not None:
        return
    if doc.doc_type == DocType.image:
        summary = llm_describe(image_path=storage.abs_path(doc.file_path))
    else:
        content_chunks = session.exec(
            select(Chunk)
            .where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
            .order_by(Chunk.chunk_index)
        ).all()
        text = "\n\n".join(c.content for c in content_chunks)[:SUMMARY_INPUT_CHARS]
        if not text.strip():
            return
        summary = llm_describe(text=text)
    doc.summary = summary
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.summary,
            content=summary,
        )
    )
    session.commit()


def _ensure_metadata_chunk(session: Session, doc: Document) -> None:
    if _has_chunks(session, doc, ChunkSource.metadata):
        return
    content = doc.title if not doc.description else f"{doc.title}\n\n{doc.description}"
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.metadata,
            content=content,
        )
    )
    session.commit()


def _next_chunk_index(session: Session, doc: Document) -> int:
    rows = session.exec(select(Chunk.chunk_index).where(Chunk.document_id == doc.id)).all()
    return (max(rows) + 1) if rows else 0


def _embed_pending_chunks(session: Session, doc: Document) -> None:
    pending = session.exec(
        select(Chunk)
        .where(Chunk.document_id == doc.id, Chunk.embedding.is_(None))
        .order_by(Chunk.chunk_index)
    ).all()
    if not pending:
        return
    vectors = llm_embed([c.content for c in pending])
    for chunk, vector in zip(pending, vectors):
        chunk.embedding = vector
        session.add(chunk)
    session.commit()


SWEEP_INTERVAL = timedelta(hours=1)
SESSION_MAX_AGE = timedelta(hours=24)


@register("sweep_scan_sessions")
def sweep_scan_sessions(session: Session, payload: dict) -> None:
    from app.models import ScanPage, ScanSession, ScanSessionStatus
    from app.services.jobs import enqueue

    storage = get_pipeline_storage()
    cutoff = datetime.now(timezone.utc) - SESSION_MAX_AGE
    for scan_session in session.exec(select(ScanSession)).all():
        finished = scan_session.status in (
            ScanSessionStatus.done,
            ScanSessionStatus.cancelled,
        )
        created = scan_session.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        abandoned = not finished and created < cutoff
        if not (finished or abandoned):
            continue
        for page in session.exec(
            select(ScanPage).where(ScanPage.session_id == scan_session.id)
        ).all():
            session.delete(page)
        session.delete(scan_session)
        session.commit()
        storage.remove_scan_session_dir(scan_session.id)

    enqueue(
        session,
        "sweep_scan_sessions",
        {},
        run_at=datetime.now(timezone.utc) + SWEEP_INTERVAL,
    )


def ensure_sweep_scheduled(engine) -> None:
    from app.models import Job, JobStatus
    from app.services.jobs import enqueue

    with Session(engine) as session:
        existing = session.exec(
            select(Job).where(
                Job.type == "sweep_scan_sessions",
                Job.status.in_([JobStatus.queued, JobStatus.running]),
            )
        ).first()
        if existing is None:
            enqueue(session, "sweep_scan_sessions", {})
