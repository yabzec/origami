from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.api.storage_errors import storage_errors
from app.models import Document, Folder
from app.services.storage import Storage, get_storage
from app.services.tree_paths import folder_rel_dir
from app.services.tree_sync import MoveLog, disk_name_taken, disk_transaction, lock_tree

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


class EnsurePath(BaseModel):
    parent_id: int | None = None
    segments: list[str] = Field(default_factory=list, max_length=64)


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
def create_folder(
    body: FolderCreate,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Folder:
    folder = Folder(name=body.name, parent_id=body.parent_id)
    with storage_errors(), disk_transaction(session, storage):
        lock_tree(session)
        session.expire_all()  # read the tree as of the lock
        if body.parent_id is not None:
            get_folder_or_404(session, body.parent_id)
        if disk_name_taken(session, body.parent_id, body.name):
            raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
        session.add(folder)
        try:
            session.flush()
        except IntegrityError:
            raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
        storage.make_dir(folder_rel_dir(session, folder.id))
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


def reparent_folder(session: Session, moves: MoveLog, folder: Folder, fields: dict) -> None:
    """Rename and/or move a folder: its directory moves and document paths under it follow.

    The caller must hold `lock_tree` (as `move_documents` does).
    """
    old_dir = folder_rel_dir(session, folder.id)
    for key, value in fields.items():
        setattr(folder, key, value)
    try:
        session.flush()
    except IntegrityError:
        raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
    new_dir = folder_rel_dir(session, folder.id)
    if new_dir != old_dir:
        under_old = func.starts_with(Document.file_path, old_dir + "/")
        session.exec(select(Document.id).where(under_old).with_for_update()).all()
        moves.move_dir(old_dir, new_dir)
        session.execute(
            update(Document)
            .where(under_old)
            .values(file_path=func.concat(new_dir, func.substr(Document.file_path, len(old_dir) + 1)))
            .execution_options(synchronize_session=False)
        )


@router.patch("/{folder_id}")
def update_folder(
    folder_id: int,
    body: FolderPatch,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Folder:
    fields = body.model_dump(exclude_unset=True)
    with storage_errors(), disk_transaction(session, storage) as moves:
        lock_tree(session)
        session.expire_all()  # read the tree as of the lock
        folder = get_folder_or_404(session, folder_id)
        if "parent_id" in fields and fields["parent_id"] is not None:
            get_folder_or_404(session, fields["parent_id"])
            if is_descendant(session, fields["parent_id"], folder_id):
                raise api_error(409, "folder_cycle", "Cannot move a folder under itself")
        new_parent = fields.get("parent_id", folder.parent_id)
        new_name = fields.get("name", folder.name)
        if disk_name_taken(session, new_parent, new_name, exclude_id=folder_id):
            raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
        reparent_folder(session, moves, folder, fields)
    session.refresh(folder)
    return folder


@router.delete("/{folder_id}", status_code=204)
def delete_folder(
    folder_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    lock_tree(session)
    session.expire_all()  # read the tree as of the lock
    folder = get_folder_or_404(session, folder_id)
    has_children = session.exec(
        select(Folder).where(Folder.parent_id == folder_id)
    ).first()
    has_documents = session.exec(
        select(Document).where(Document.folder_id == folder_id)
    ).first()
    if has_children or has_documents:
        raise api_error(409, "folder_not_empty", "Folder contains items")
    rel = folder_rel_dir(session, folder_id)
    session.delete(folder)
    session.commit()
    storage.remove_dir(rel)


def _child_folder_id(session: Session, storage: Storage, parent_id: int | None, name: str) -> int:
    lock_tree(session)  # held until the commit below (or the end of the request)
    session.expire_all()  # read the tree as of the lock
    same_parent = Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id
    existing = session.exec(select(Folder).where(same_parent, Folder.name == name)).first()
    if existing is not None:
        return existing.id
    if disk_name_taken(session, parent_id, name):
        raise api_error(409, "duplicate_folder", f"A sibling folder maps to the same name as {name!r}")
    folder = Folder(name=name, parent_id=parent_id)
    try:
        with session.begin_nested():
            session.add(folder)
            session.flush()
    except IntegrityError:  # created concurrently by a parallel upload
        return session.exec(select(Folder).where(same_parent, Folder.name == name)).one().id
    with storage_errors():
        storage.make_dir(folder_rel_dir(session, folder.id))
    session.commit()
    return folder.id


@router.post("/ensure-path")
def ensure_path(
    body: EnsurePath,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Leaf folder of `segments` under `parent_id`, creating missing folders (idempotent)."""
    current = body.parent_id
    if current is not None:
        get_folder_or_404(session, current)
    names = [segment.strip() for segment in body.segments]
    if any(not name for name in names):
        raise api_error(422, "validation_error", "Folder names must not be empty")
    for name in names:
        current = _child_folder_id(session, storage, current, name)
    return {"folder_id": current}
