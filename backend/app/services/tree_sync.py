"""Keep the storage tree in step with the database: moves happen inside the DB transaction."""

import logging
import uuid
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import BinaryIO

from sqlmodel import Session, select

from app.models import Document, Folder
from app.services.storage import Storage
from app.services.tree_paths import document_rel_path, safe_name

log = logging.getLogger("origami.tree")


class MoveLog:
    """Disk moves done in the current transaction, undone in reverse order on failure."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.done: list[tuple[str, str, str]] = []

    def move_file(self, old: str, new: str) -> None:
        if old != new and self.storage.move_file(old, new):
            self.done.append(("file", old, new))

    def move_dir(self, old: str, new: str) -> None:
        if old != new and self.storage.move_dir(old, new):
            self.done.append(("dir", old, new))

    def undo(self) -> None:
        for kind, old, new in reversed(self.done):
            try:
                if kind == "file":
                    self.storage.move_file(new, old)
                else:
                    self.storage.move_dir(new, old)
            except Exception:
                log.critical(
                    "Could not move %s back to %s; run: python -m app.cli migrate-storage --check",
                    new, old, exc_info=True,
                )
        self.done.clear()


@contextmanager
def disk_transaction(session: Session, storage: Storage):
    """Yield a MoveLog; commit at the end, or roll back and move files back on any error."""
    moves = MoveLog(storage)
    try:
        yield moves
        session.commit()
    except BaseException:
        session.rollback()
        moves.undo()
        raise


def lock_documents(session: Session, ids: list[uuid.UUID]) -> list[Document]:
    """Row-lock documents (same lock the worker takes before writing a file)."""
    if not ids:
        return []
    return list(
        session.exec(
            select(Document)
            .where(Document.id.in_(ids))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def relocate_document(session: Session, storage: Storage, moves: MoveLog, doc: Document) -> None:
    """Move the document file to the path its current title and folder give."""
    if not doc.file_path:
        return
    session.flush()  # earlier relocations in this transaction count as taken names
    new = document_rel_path(session, storage, doc, PurePosixPath(doc.file_path).suffix)
    if new != doc.file_path:
        moves.move_file(doc.file_path, new)
        doc.file_path = new


def write_document_file(
    session: Session, storage: Storage, doc: Document, ext: str, data: bytes | BinaryIO
) -> None:
    """Write the document file under the row lock (serialized with renames), then commit."""
    session.flush()
    session.refresh(doc, with_for_update=True)
    ext = ext.lower()
    old = doc.file_path
    if old and PurePosixPath(old).suffix.lower() == ext:
        rel = old
    else:
        rel = document_rel_path(session, storage, doc, ext)
    try:
        rel, size = storage.write_file(rel, data)
        doc.file_path = rel
        doc.file_size = size
        session.commit()
    except BaseException:
        session.rollback()
        if rel != old:
            storage.delete_file(rel)
        raise
    if old and old != rel:
        storage.delete_file(old)


def disk_name_taken(
    session: Session, parent_id: int | None, name: str, exclude_id: int | None = None
) -> bool:
    """True when a sibling folder already maps to the same directory name on disk."""
    same_parent = Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id
    target = safe_name(name)
    return any(
        f.id != exclude_id and safe_name(f.name) == target
        for f in session.exec(select(Folder).where(same_parent))
    )
