import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.models import Document, DocumentTag, Folder, Tag
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/documents", tags=["documents"], dependencies=[Depends(get_current_user)]
)


class DocumentPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    folder_id: int | None = None
    tag_ids: list[int] | None = None


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


@router.patch("/{document_id}")
def update_document(
    document_id: uuid.UUID,
    body: DocumentPatch,
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    fields = body.model_dump(exclude_unset=True)
    tag_ids = fields.pop("tag_ids", None)

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
    storage.delete_document_file(doc.file_path)
    session.delete(doc)  # chunks and document_tags cascade via FK
    session.commit()
