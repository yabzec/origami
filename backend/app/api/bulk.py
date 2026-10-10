"""Bulk actions on a mixed selection of folders and documents, each in one transaction."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.api.documents import BULK_MAX, delete_documents, move_documents
from app.api.folders import check_reserved_name, get_folder_or_404, is_descendant, reparent_folder
from app.api.storage_errors import storage_errors
from app.db import get_session
from app.models import Document, Folder
from app.services.storage import Storage, get_storage
from app.services.tree_paths import folder_rel_dir, safe_name
from app.services.tree_sync import disk_name_taken, disk_transaction, lock_documents, lock_tree

router = APIRouter(prefix="/api/bulk", tags=["bulk"], dependencies=[Depends(get_current_user)])


class BulkItems(BaseModel):
    folder_ids: list[int] = Field(default_factory=list, max_length=BULK_MAX)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=BULK_MAX)

    @model_validator(mode="after")
    def not_empty(self):
        if not self.folder_ids and not self.document_ids:
            raise ValueError("Select at least one folder or document")
        return self


class BulkItemsMove(BulkItems):
    folder_id: int | None


def existing_folders(session: Session, ids: list[int]) -> tuple[list[int], list[int]]:
    """(found, missing), de-duplicated, in input order."""
    ids = list(dict.fromkeys(ids))
    found = set(session.exec(select(Folder.id).where(Folder.id.in_(ids))).all()) if ids else set()
    return [i for i in ids if i in found], [i for i in ids if i not in found]


def subtree_ids(session: Session, root_ids: list[int]) -> list[int]:
    """root_ids and all their descendants, breadth-first: every folder comes after its parent."""
    order = list(dict.fromkeys(root_ids))
    seen = set(order)
    frontier = order
    while frontier:
        children = session.exec(select(Folder.id).where(Folder.parent_id.in_(frontier))).all()
        frontier = [c for c in children if c not in seen]
        seen.update(frontier)
        order.extend(frontier)
    return order


def outermost(session: Session, folder_ids: list[int]) -> list[int]:
    """Drop selected folders that lie inside another selected folder (they move with it)."""
    selected = set(folder_ids)
    result = []
    for folder_id in folder_ids:
        parent = session.get(Folder, folder_id).parent_id
        while parent is not None and parent not in selected:
            parent = session.get(Folder, parent).parent_id
        if parent is None:
            result.append(folder_id)
    return result


@router.post("/move")
def bulk_move_items(
    body: BulkItemsMove,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    destination = body.folder_id
    with storage_errors(), disk_transaction(session, storage) as moves:
        lock_tree(session)
        session.expire_all()  # read the tree as of the lock
        if destination is not None:
            get_folder_or_404(session, destination)
        found, missing_folders = existing_folders(session, body.folder_ids)
        roots = outermost(session, found)
        if destination is not None and any(is_descendant(session, destination, f) for f in roots):
            raise api_error(409, "folder_cycle", "Cannot move a folder into itself or one of its subfolders")
        moving = [session.get(Folder, f) for f in roots]
        moving = [f for f in moving if f.parent_id != destination]  # already there: nothing to do
        incoming: set[str] = set()
        for folder in moving:
            check_reserved_name(destination, folder.name)
            key = safe_name(folder.name)
            if key in incoming or disk_name_taken(session, destination, folder.name, exclude_id=folder.id):
                raise api_error(
                    409, "duplicate_folder", f"The destination already has a folder named {folder.name!r}"
                )
            incoming.add(key)
        for folder in moving:
            reparent_folder(session, moves, folder, {"parent_id": destination})
        inside = set(subtree_ids(session, roots))
        docs = lock_documents(session, body.document_ids)
        found_docs = {d.id for d in docs}
        move_documents(session, storage, moves, [d for d in docs if d.folder_id not in inside], destination)
    return {
        "moved_folders": len(found),
        "moved_documents": len(docs),
        "missing_folders": missing_folders,
        "missing_documents": [str(i) for i in body.document_ids if i not in found_docs],
    }


@router.post("/delete")
def bulk_delete_items(
    body: BulkItems,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    lock_tree(session)
    session.expire_all()  # read the tree as of the lock
    found, missing_folders = existing_folders(session, body.folder_ids)
    deepest_first = list(reversed(subtree_ids(session, found)))
    dirs = [folder_rel_dir(session, f) for f in deepest_first]
    inside = (
        session.exec(select(Document.id).where(Document.folder_id.in_(deepest_first))).all()
        if deepest_first
        else []
    )
    docs = lock_documents(session, list(dict.fromkeys([*body.document_ids, *inside])))
    found_docs = {d.id for d in docs}
    files = delete_documents(session, docs)
    session.flush()  # document rows go before the folders they reference
    for folder_id in deepest_first:
        session.delete(session.get(Folder, folder_id))
        session.flush()  # children before parents (self-referencing foreign key)
    session.commit()
    for file_rel, preview, doc_id in files:
        storage.delete_document_files(file_rel, preview, doc_id)
    for rel in dirs:
        storage.remove_dir(rel)  # rmdir only: a directory with untracked files stays
    return {
        "deleted_folders": len(deepest_first),
        "deleted_documents": len(docs),
        "missing_folders": missing_folders,
        "missing_documents": [str(i) for i in body.document_ids if i not in found_docs],
    }
