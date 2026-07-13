import logging
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
from app.services.ocr import ocr_image, pdf_to_searchable_pdf
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

    if doc.doc_type == DocType.scan:
        return _extract_scan(session, doc, storage, payload)  # Task 9

    raise ValueError(f"Unknown doc_type {doc.doc_type!r}")


def _extract_scan(session, doc, storage, payload):  # implemented in Task 9
    raise NotImplementedError("scan compilation lands in the scan-compile task")


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
