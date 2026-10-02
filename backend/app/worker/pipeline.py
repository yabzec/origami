import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.config import get_primary_language, get_settings
from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, TranslationStatus
from app.services.chunking import chunk_pages
from app.services.extract import (
    extract_docx,
    extract_pdf_text,
    extract_text_file,
    pdf_needs_ocr,
)
from app.services.llm import describe as llm_describe
from app.services.llm import embed as llm_embed
from app.services.llm import translate as llm_translate
from app.services.ocr import images_to_pdf, images_to_searchable_pdf, ocr_image, pdf_to_searchable_pdf
from app.services.storage import Storage
from app.worker.runner import register

log = logging.getLogger("origami.pipeline")

SUMMARY_INPUT_CHARS = 8000
IMAGE_SUMMARY_TEXT_THRESHOLD = 40


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
        _ensure_translation(session, doc)
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
        return _extract_scan(session, doc, storage, payload)

    path = storage.abs_path(doc.file_path)

    if doc.doc_type == DocType.text:
        text = extract_docx(path) if path.suffix == ".docx" else extract_text_file(path)
        return [(None, text)]

    if doc.doc_type == DocType.image:
        if not doc.ocr_enabled:
            return []  # photo path: no OCR, no companion pdf; summary via vision
        pdf_bytes, text = ocr_image(path, doc.ocr_languages)
        storage.store_file(doc.id, ".pdf", pdf_bytes)  # companion searchable PDF
        return [(1, text)]

    if doc.doc_type == DocType.pdf:
        pages = extract_pdf_text(path)
        if doc.ocr_enabled and pdf_needs_ocr(pages):
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
    # Retry after the PDF was already built: recover text from the stored PDF instead of re-compiling from scans.
    if doc.file_path and storage.abs_path(doc.file_path).exists():
        pages = extract_pdf_text(storage.abs_path(doc.file_path))
    else:
        page_rows = session.exec(
            select(ScanPage)
            .where(ScanPage.session_id == session_id)
            .order_by(ScanPage.page_number)
        ).all()
        image_paths = [storage.abs_path(p.image_path) for p in page_rows]
        if doc.ocr_enabled:
            pdf_bytes, pages = images_to_searchable_pdf(image_paths, doc.ocr_languages)
        else:
            pdf_bytes = images_to_pdf(image_paths)
            pages = []
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


def _content_chunks(session: Session, doc: Document) -> list[Chunk]:
    return list(
        session.exec(
            select(Chunk)
            .where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
            .order_by(Chunk.chunk_index)
        ).all()
    )


def _ensure_summary(session: Session, doc: Document, storage: Storage) -> None:
    if doc.doc_type == DocType.video or doc.summary is not None:
        return
    text = "\n\n".join(c.content for c in _content_chunks(session, doc)).strip()
    if doc.doc_type == DocType.image and len(text) < IMAGE_SUMMARY_TEXT_THRESHOLD:
        # No usable extracted text (a photo, or no-OCR) — send the file to vision.
        result = llm_describe(image_path=storage.abs_path(doc.file_path))
    elif text:
        result = llm_describe(text=text[:SUMMARY_INPUT_CHARS])
    else:
        return  # nothing to summarize (e.g. a no-OCR scan or an empty PDF)
    doc.summary = result.summary
    doc.detected_language = result.language
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.summary,
            content=result.summary,
        )
    )
    session.commit()


def _ensure_translation(session: Session, doc: Document) -> None:
    target = get_primary_language()
    if not doc.detected_language or doc.detected_language == target:
        return
    if doc.translation_status == TranslationStatus.done and _has_chunks(
        session, doc, ChunkSource.translation
    ):
        return
    content_chunks = _content_chunks(session, doc)
    if not content_chunks:
        return
    try:
        # translate everything first so a failure never leaves partial translation chunks
        translated = [(c, llm_translate(c.content, target)) for c in content_chunks]
    except Exception:
        log.exception("Translation failed for document %s", doc.id)
        doc.translation_status = TranslationStatus.failed
        session.commit()
        return
    next_index = _next_chunk_index(session, doc)
    for offset, (chunk, text) in enumerate(translated):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=next_index + offset,
                page_number=chunk.page_number,
                source=ChunkSource.translation,
                content=text,
            )
        )
    doc.translation_status = TranslationStatus.done
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
    if len(vectors) != len(pending):
        raise ValueError(
            f"Expected {len(pending)} embeddings from llm_embed, got {len(vectors)}"
        )
    for chunk, vector in zip(pending, vectors):
        chunk.embedding = vector
        session.add(chunk)
    session.commit()


SWEEP_INTERVAL = timedelta(hours=1)
SESSION_MAX_AGE = timedelta(hours=24)


@register("sweep_scan_sessions")
def sweep_scan_sessions(session: Session, payload: dict) -> None:
    from app.models import Job, JobStatus, ScanPage, ScanSession, ScanSessionStatus
    from app.services.jobs import enqueue

    storage = get_pipeline_storage()
    cutoff = datetime.now(timezone.utc) - SESSION_MAX_AGE

    # Sessions with a queued/running process_document job must survive the sweep even
    # if they look "abandoned" by age, otherwise a backlogged worker could have its
    # temp page files deleted out from under an in-flight compile job.
    in_flight_jobs = session.exec(
        select(Job).where(
            Job.type == "process_document",
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
    ).all()
    protected_session_ids = {
        job.payload.get("scan_session_id")
        for job in in_flight_jobs
        if job.payload.get("scan_session_id") is not None
    }

    for scan_session in session.exec(select(ScanSession)).all():
        finished = scan_session.status in (
            ScanSessionStatus.done,
            ScanSessionStatus.cancelled,
        )
        created = scan_session.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        abandoned = not finished and created < cutoff
        if (
            abandoned
            and scan_session.status == ScanSessionStatus.compiling
            and scan_session.id in protected_session_ids
        ):
            continue
        if not (finished or abandoned):
            continue
        for page in session.exec(
            select(ScanPage).where(ScanPage.session_id == scan_session.id)
        ).all():
            session.delete(page)
        sid = scan_session.id
        session.delete(scan_session)
        session.commit()
        storage.remove_scan_session_dir(sid)

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
