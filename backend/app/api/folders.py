from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.models import Document, Folder

router = APIRouter(
    prefix="/api/folders", tags=["folders"], dependencies=[Depends(get_current_user)]
)


class FolderCreate(BaseModel):
    name: str
    parent_id: int | None = None


class FolderPatch(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    model_config = {"json_schema_extra": {"note": "parent_id explicit null = move to root"}}


def get_folder_or_404(session: Session, folder_id: int) -> Folder:
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise api_error(404, "not_found", f"Folder {folder_id} not found")
    return folder


def is_descendant(session: Session, candidate_id: int, ancestor_id: int) -> bool:
    """True if candidate_id is ancestor_id or lies in its subtree."""
    current: int | None = candidate_id
    while current is not None:
        if current == ancestor_id:
            return True
        current = session.get(Folder, current).parent_id
    return False


@router.post("", status_code=201)
def create_folder(body: FolderCreate, session: Session = Depends(get_session)) -> Folder:
    if body.parent_id is not None:
        get_folder_or_404(session, body.parent_id)
    folder = Folder(name=body.name, parent_id=body.parent_id)
    session.add(folder)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
    session.refresh(folder)
    return folder


@router.get("")
def list_folders(session: Session = Depends(get_session)) -> list[dict]:
    counts = dict(
        session.exec(
            select(Document.folder_id, func.count())
            .where(Document.folder_id.is_not(None))
            .group_by(Document.folder_id)
        ).all()
    )
    return [{**f.model_dump(), "document_count": counts.get(f.id, 0)} for f in session.exec(select(Folder))]


@router.patch("/{folder_id}")
def update_folder(
    folder_id: int, body: FolderPatch, session: Session = Depends(get_session)
) -> Folder:
    folder = get_folder_or_404(session, folder_id)
    fields = body.model_dump(exclude_unset=True)
    if "parent_id" in fields and fields["parent_id"] is not None:
        get_folder_or_404(session, fields["parent_id"])
        if is_descendant(session, fields["parent_id"], folder_id):
            raise api_error(409, "folder_cycle", "Cannot move a folder under itself")
    for key, value in fields.items():
        setattr(folder, key, value)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
    session.refresh(folder)
    return folder


@router.delete("/{folder_id}", status_code=204)
def delete_folder(folder_id: int, session: Session = Depends(get_session)) -> None:
    folder = get_folder_or_404(session, folder_id)
    has_children = session.exec(
        select(Folder).where(Folder.parent_id == folder_id)
    ).first()
    has_documents = session.exec(
        select(Document).where(Document.folder_id == folder_id)
    ).first()
    if has_children or has_documents:
        raise api_error(409, "folder_not_empty", "Folder contains items")
    session.delete(folder)
    session.commit()
