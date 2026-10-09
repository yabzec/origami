import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, update
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Session, select

from app.config import get_settings
from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job, TranslationSegment, TranslationStatus
from app.services.chunking import chunk_pages, page_texts_from_chunks, segment_pages
from app.services.convert import OFFICE_EXTENSIONS, ConversionError, office_to_pdf
from app.services.extract import (
    extract_docx,
    extract_pdf_text,
    extract_text_file,
    pdf_needs_ocr,
)
from app.services.language import detect_language
from app.services.llm import describe as llm_describe
from app.services.llm import embed as llm_embed
from app.services.llm import TranslationDeferred
from app.services.llm import translate as llm_translate
from app.services.ocr import images_to_pdf, images_to_searchable_pdf, ocr_image, pdf_to_searchable_pdf
from app.services.storage import Storage, companion_name, get_storage, preview_name
from app.services.tree_sync import write_document_file
from app.worker.runner import is_final_attempt, register

log = logging.getLogger("origami.pipeline")

SUMMARY_INPUT_CHARS = 8000
IMAGE_SUMMARY_TEXT_THRESHOLD = 40
EMBED_BATCH_SIZE = 100  # provider batch limit; translation doubles chunk count


def get_pipeline_storage() -> Storage:
    """Worker-side storage factory (no FastAPI DI in the worker process)."""
    return get_storage()


@register("process_document")
def process_document(session: Session, payload: dict) -> None:
    doc = session.get(Document, payload["document_id"])
    if doc is None:
        log.info("process_document: document %s no longer exists", payload["document_id"])
        return
    storage = get_pipeline_storage()
    try:
        doc.status = DocStatus.processing
        session.commit()

        pages = _extract_content(session, doc, storage, payload)
        _ensure_content_chunks(session, doc, pages)
        _ensure_language(session, doc)
        _ensure_summary(session, doc, storage)
        _ensure_metadata_chunk(session, doc)
        _embed_pending_chunks(session, doc)

        doc.status = DocStatus.ready
        doc.error_message = None
        _schedule_translation(session, doc)  # same commit as "ready"
        session.commit()
    except Exception as exc:
        session.rollback()
        if is_final_attempt(payload):
            doc.status = DocStatus.failed
            doc.error_message = str(exc)[:2000]
        else:
            # more attempts follow: show "waiting", not "failed"
            doc.status = DocStatus.pending
            doc.error_message = "Retrying: " + str(exc)[:2000]
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

    if (
        payload.get("force_ocr")
        and doc.doc_type in (DocType.pdf, DocType.scan)
        and doc.file_path
        and storage.abs_path(doc.file_path).exists()
    ):
        return _reocr_pdf(session, doc, storage)

    if doc.doc_type == DocType.scan:
        return _extract_scan(session, doc, storage, payload)

    path = storage.abs_path(doc.file_path)

    if doc.doc_type == DocType.text:
        if path.suffix in OFFICE_EXTENSIONS:
            return _extract_office(session, doc, storage, path)
        return [(None, extract_text_file(path))]

    if doc.doc_type == DocType.image:
        if not doc.ocr_enabled:
            return []  # photo path: no OCR, no companion pdf; summary via vision
        pdf_bytes, text = ocr_image(path, doc.ocr_languages)
        storage.write_derived(companion_name(doc.id), pdf_bytes)  # companion searchable PDF
        return [(1, text)]

    if doc.doc_type == DocType.pdf:
        pages = extract_pdf_text(path)
        if doc.ocr_enabled and pdf_needs_ocr(pages):
            pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
            write_document_file(session, storage, doc, ".pdf", pdf_bytes)
            doc.ocr_applied = True
        else:
            doc.ocr_applied = False  # original file kept
        doc.page_count = len(pages)
        session.commit()
        return pages

    raise ValueError(f"Unknown doc_type {doc.doc_type!r}")


def _extract_office(
    session: Session, doc: Document, storage: Storage, path: Path
) -> list[tuple[int | None, str]]:
    """Office files are viewed and indexed through a LibreOffice PDF; the original stays the download."""
    preview = storage.derived_abs(doc.preview_path) if doc.preview_path else None
    if preview is None or not preview.is_file():
        try:
            pdf_bytes = office_to_pdf(path)
        except ConversionError:
            if path.suffix != ".docx":
                raise  # no other parser for .doc/.odt/.rtf: the document ends failed
            log.warning("Office conversion failed for %s; using python-docx text", doc.id, exc_info=True)
            doc.preview_path = None
            session.commit()
            return [(None, extract_docx(path))]
        doc.preview_path = storage.write_derived(preview_name(doc.id), pdf_bytes)
        preview = storage.derived_abs(doc.preview_path)
    pages = extract_pdf_text(preview)
    doc.page_count = len(pages)
    session.commit()
    return pages


def _reocr_pdf(session: Session, doc: Document, storage: Storage) -> list[tuple[int | None, str]]:
    """Re-process: OCR the stored PDF again (scan page images are gone after compile).

    A born-digital PDF (ocr_applied False) is never rasterized unless its own text layer is poor;
    unknown provenance (None, legacy rows) is treated as previously OCR'd.
    """
    path = storage.abs_path(doc.file_path)
    pages = extract_pdf_text(path)
    if doc.ocr_enabled and (doc.ocr_applied is not False or pdf_needs_ocr(pages)):
        pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
        write_document_file(session, storage, doc, ".pdf", pdf_bytes)
        doc.ocr_applied = True
    doc.page_count = len(pages)
    session.commit()
    return pages


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
        image_paths = [storage.tmp_abs(p.image_path) for p in page_rows]
        if doc.ocr_enabled:
            pdf_bytes, pages = images_to_searchable_pdf(image_paths, doc.ocr_languages)
        else:
            pdf_bytes = images_to_pdf(image_paths)
            pages = []
        write_document_file(session, storage, doc, ".pdf", pdf_bytes)
        doc.ocr_applied = doc.ocr_enabled
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


def _ensure_language(session: Session, doc: Document) -> None:
    """Local detection on the extracted text; translation depends on this, not on the summary."""
    if doc.detected_language is not None:
        return
    text = "\n\n".join(c.content for c in _content_chunks(session, doc))
    language = detect_language(text)
    if language is not None:
        doc.detected_language = language
        session.commit()


def _ensure_summary(session: Session, doc: Document, storage: Storage) -> None:
    if doc.doc_type == DocType.video or doc.summary is not None or not doc.summary_enabled:
        return
    text = "\n\n".join(c.content for c in _content_chunks(session, doc)).strip()
    if doc.doc_type == DocType.image and len(text) < IMAGE_SUMMARY_TEXT_THRESHOLD:
        # No usable extracted text (a photo, or no-OCR) — send the file to vision.
        summary = llm_describe(image_path=storage.abs_path(doc.file_path))
    elif text:
        summary = llm_describe(text=text[:SUMMARY_INPUT_CHARS])
    else:
        return  # nothing to summarize (e.g. a no-OCR scan or an empty PDF)
    if not summary:
        return  # empty LLM reply: never store an empty summary chunk
    doc.summary = summary
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.summary,
            content=summary,
        )
    )
    # AI text; the UI labels it until the user edits it. Conditional in SQL so a
    # description the user saved during the LLM call is never overwritten.
    session.execute(
        update(Document)
        .where(Document.id == doc.id, func.btrim(func.coalesce(Document.description, "")) == "")
        .values(description=summary)
    )
    session.commit()
    session.refresh(doc)


def _needs_translation(session: Session, doc: Document) -> bool:
    if not doc.translation_enabled:
        return False
    if not doc.detected_language or doc.detected_language == doc.translation_language:
        return False
    if doc.translation_status == TranslationStatus.done and _has_chunks(
        session, doc, ChunkSource.translation
    ):
        return False
    return _has_chunks(session, doc, ChunkSource.content)


def _schedule_translation(session: Session, doc: Document) -> None:
    """Queue translate_document in the caller's transaction (no commit here)."""
    if not _needs_translation(session, doc):
        return
    doc.translation_status = TranslationStatus.pending
    session.add(
        Job(
            type="translate_document",
            payload={"document_id": str(doc.id)},
            run_at=datetime.now(timezone.utc),
        )
    )


CHARS_PER_TOKEN = 3.5
SEGMENT_SHARE_OF_TPM = 0.4  # input share of one minute's budget; output roughly doubles it


def translation_segment_chars() -> int:
    settings = get_settings()
    limit = settings.translation_segment_chars
    if settings.llm_tpm_limit > 0:
        limit = min(limit, int(settings.llm_tpm_limit * SEGMENT_SHARE_OF_TPM * CHARS_PER_TOKEN))
    return max(limit, 200)


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def delete_translation_segments(session: Session, doc_id) -> None:
    """Drop saved translation progress (no commit)."""
    session.exec(delete(TranslationSegment).where(TranslationSegment.document_id == doc_id))


def _translate_segments(
    session: Session, doc: Document, content_chunks: list[Chunk]
) -> list[tuple[int | None, str]]:
    """Translate page segments, committing each one so a retry resumes where this run stopped."""
    target = doc.translation_language
    pages = page_texts_from_chunks([(c.page_number, c.content) for c in content_chunks])
    stored = {
        s.segment_index: s
        for s in session.exec(select(TranslationSegment).where(TranslationSegment.document_id == doc.id))
    }
    translated: list[tuple[int | None, str]] = []
    for index, (page_number, source) in enumerate(segment_pages(pages, translation_segment_chars())):
        digest = _source_hash(f"{target}\n{source}")  # a new primary language invalidates segments
        row = stored.get(index)
        if row is None or row.source_hash != digest:
            text = llm_translate(source, target)
            row = row or TranslationSegment(document_id=doc.id, segment_index=index, source_hash=digest, text="")
            row.page_number, row.source_hash, row.text = page_number, digest, text
            session.add(row)
            session.commit()
        translated.append((row.page_number, row.text))
    return translated


def _merge_pages(segments: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    merged: list[tuple[int | None, str]] = []
    for page_number, text in segments:
        if merged and merged[-1][0] == page_number:
            merged[-1] = (page_number, f"{merged[-1][1]}\n\n{text}")
        else:
            merged.append((page_number, text))
    return merged


def _insert_translation_chunks(session: Session, doc: Document) -> bool:
    """Translate page segments (resumable), then insert the translation chunks in one commit.

    Returns False without writing chunks when the document was re-processed meanwhile
    (translation status reset or content chunks replaced): the new run schedules its own job.
    """
    content_chunks = _content_chunks(session, doc)
    source_ids = [c.id for c in content_chunks]
    translated = _merge_pages(_translate_segments(session, doc, content_chunks))
    # row lock (released by the commit below) serializes with reprocess_document, which takes
    # the same lock before touching chunks; never held across the LLM calls above
    session.refresh(doc, with_for_update=True)
    current_ids = [c.id for c in _content_chunks(session, doc)]
    if not source_ids or current_ids != source_ids or doc.translation_status != TranslationStatus.pending:
        session.rollback()  # release the row lock
        log.info("Document %s changed during translation; result discarded", doc.id)
        return False
    next_index = _next_chunk_index(session, doc)
    for offset, chunk in enumerate(chunk_pages(translated)):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=next_index + offset,
                page_number=chunk["page_number"],
                source=ChunkSource.translation,
                content=chunk["content"],
            )
        )
    delete_translation_segments(session, doc.id)
    session.commit()
    return True


def _finish_translation(session: Session, doc: Document, status: str) -> bool:
    """Set translation_status only if it is still pending (row-locked); False = superseded."""
    session.refresh(doc, with_for_update=True)
    if doc.translation_status != TranslationStatus.pending:
        session.rollback()
        log.info("Document %s changed during translation; result discarded", doc.id)
        return False
    doc.translation_status = status
    session.commit()
    return True


def _defer_translation(session: Session, doc: Document, wait: float) -> None:
    """Queue a later translate_document run if the translation is still pending (row-locked)."""
    session.refresh(doc, with_for_update=True)
    if doc.translation_status != TranslationStatus.pending:
        session.rollback()
        log.info("Document %s changed during translation; deferral dropped", doc.id)
        return
    session.add(
        Job(
            type="translate_document",
            payload={"document_id": str(doc.id)},
            run_at=datetime.now(timezone.utc) + timedelta(seconds=wait),
        )
    )
    session.commit()
    log.info("Translation of %s rate-limited; continuing in %.0fs", doc.id, wait)


@register("translate_document")
def translate_document(session: Session, payload: dict) -> None:
    doc = session.get(Document, payload["document_id"])
    if doc is None:
        log.info("translate_document: document %s no longer exists", payload["document_id"])
        return
    if not doc.detected_language or doc.detected_language == doc.translation_language:
        # nothing to translate (e.g. the document was re-processed since queueing)
        if doc.translation_status == TranslationStatus.pending:
            _finish_translation(session, doc, None)  # stop the UI polling "pending"
        return
    if not doc.translation_enabled or not _has_chunks(session, doc, ChunkSource.content):
        # disabled, or no extracted text (e.g. an image summarised by vision)
        if doc.translation_status == TranslationStatus.pending:
            _finish_translation(session, doc, None)
        return
    if doc.translation_status == TranslationStatus.done and _has_chunks(
        session, doc, ChunkSource.translation
    ):
        return
    try:
        # a retry after an embedding error reuses the stored translation chunks
        if not _has_chunks(session, doc, ChunkSource.translation):
            if not _insert_translation_chunks(session, doc):
                return
        _embed_pending_chunks(session, doc)  # only translation chunks are still unembedded
        _finish_translation(session, doc, TranslationStatus.done)
    except TranslationDeferred as deferred:
        # long provider wait: free the worker; saved segments let the next run resume
        session.rollback()
        _defer_translation(session, doc, deferred.wait)
    except StaleDataError:
        # re-process deleted the chunks while they were being embedded: superseded run
        session.rollback()
        log.info("Document %s re-processed during translation; run discarded", doc.id)
    except Exception:
        session.rollback()
        status = TranslationStatus.failed if is_final_attempt(payload) else TranslationStatus.pending
        if _finish_translation(session, doc, status):
            raise
        # superseded: complete the job quietly (no retry, no failure email)


def _ensure_metadata_chunk(session: Session, doc: Document) -> None:
    if _has_chunks(session, doc, ChunkSource.metadata):
        return
    if not doc.description or doc.description == doc.summary:
        content = doc.title  # an AI description is already embedded as the summary chunk
    else:
        content = f"{doc.title}\n\n{doc.description}"
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
    for start in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[start : start + EMBED_BATCH_SIZE]
        vectors = llm_embed([c.content for c in batch])
        if len(vectors) != len(batch):
            raise ValueError(
                f"Expected {len(batch)} embeddings from llm_embed, got {len(vectors)}"
            )
        for chunk, vector in zip(batch, vectors):
            chunk.embedding = vector
            session.add(chunk)
    session.commit()


SWEEP_INTERVAL = timedelta(hours=1)
SESSION_MAX_AGE = timedelta(hours=24)
PART_FILE_MAX_AGE = timedelta(hours=1)


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

    for part in storage.remove_part_files(PART_FILE_MAX_AGE.total_seconds()):
        log.info("Removed leftover partial write %s", part)

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
