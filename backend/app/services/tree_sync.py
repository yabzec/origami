"""Keep the storage tree in step with the database: moves happen inside the DB transaction."""

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from sqlalchemy import text
from sqlmodel import Session, select

from app.models import Document, DocumentAttachment, Folder
from app.services.storage import Storage
from app.services.tree_paths import (
    bundle_dir_names,
    bundle_rel_dir,
    document_rel_path,
    has_attachments,
    join_rel,
    safe_name,
    unique_name,
)

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
                    "Could not move %s back to %s; run: python -m app.cli check-storage",
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


# Fixed key of the transaction-scoped advisory lock that serializes every tree path computation.
TREE_LOCK_KEY = 7_202_610_009


def lock_tree(session: Session) -> None:
    """Serialize path computations and tree moves until this transaction ends (re-entrant)."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": TREE_LOCK_KEY})


def lock_documents(session: Session, ids: list[uuid.UUID]) -> list[Document]:
    """Row-lock documents (same lock the worker takes before writing a file)."""
    if not ids:
        return []
    return list(
        session.exec(
            select(Document)
            .where(Document.id.in_(ids))
            .order_by(Document.id)  # one lock order for every caller
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def relocate_document(session: Session, storage: Storage, moves: MoveLog, doc: Document) -> None:
    """Move the document file (or its bundle directory) to the path its current title and folder give."""
    if not doc.file_path:
        return
    session.flush()  # earlier relocations in this transaction count as taken names
    if has_attachments(session, doc.id):
        _relocate_bundle(session, storage, moves, doc)
        return
    new = document_rel_path(session, storage, doc, PurePosixPath(doc.file_path).suffix)
    if new != doc.file_path:
        moves.move_file(doc.file_path, new)
        doc.file_path = new


def _doc_attachments(session: Session, doc: Document) -> list[DocumentAttachment]:
    return list(
        session.exec(
            select(DocumentAttachment)
            .where(DocumentAttachment.document_id == doc.id)
            .order_by(DocumentAttachment.id)
        )
    )


def _relocate_bundle(session: Session, storage: Storage, moves: MoveLog, doc: Document) -> None:
    old_dir = str(PurePosixPath(doc.file_path).parent)
    new_dir = bundle_rel_dir(session, storage, doc)
    attachments = _doc_attachments(session, doc)
    if new_dir != old_dir:
        moves.move_dir(old_dir, new_dir)
        for attachment in attachments:
            attachment.file_path = join_rel(new_dir, attachment.filename)
    old_name = PurePosixPath(doc.file_path).name
    name = unique_name(
        storage.abs_path(new_dir),
        PurePosixPath(new_dir).name,
        PurePosixPath(doc.file_path).suffix,
        {a.filename for a in attachments},
        own=old_name,
    )
    if name != old_name:
        moves.move_file(join_rel(new_dir, old_name), join_rel(new_dir, name))
    doc.file_path = join_rel(new_dir, name)


def write_document_file(
    session: Session, storage: Storage, doc: Document, ext: str, data: bytes | BinaryIO
) -> None:
    """Write the document file under the tree and row locks (serialized with renames), then commit."""
    lock_tree(session)  # first, so the lock order is always tree lock, then row locks
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


@dataclass
class AttachmentSource:
    role: str  # AttachmentRole
    filename: str  # wanted name; " (n)" is added on a clash
    source: Path
    page_count: int | None = None


def write_bundle(
    session: Session,
    storage: Storage,
    doc: Document,
    ext: str,
    data: bytes,
    attachments: list[AttachmentSource],
) -> None:
    """Write a new bundle directory (main file plus attachments) for a document, then commit."""
    lock_tree(session)  # same lock order as write_document_file
    session.flush()
    session.refresh(doc, with_for_update=True)
    bundle = bundle_rel_dir(session, storage, doc)
    bundle_abs = storage.abs_path(bundle)
    names: list[str] = []
    for attachment in attachments:
        wanted = PurePosixPath(attachment.filename)
        names.append(unique_name(bundle_abs, wanted.stem, wanted.suffix.lower(), set(names)))
    main = unique_name(bundle_abs, PurePosixPath(bundle).name, ext.lower(), set(names))
    written: list[str] = []
    try:
        rel, size = storage.write_file(join_rel(bundle, main), data)
        written.append(rel)
        doc.file_path, doc.file_size = rel, size
        for attachment, name in zip(attachments, names):
            with attachment.source.open("rb") as src:
                att_rel, att_size = storage.write_file(join_rel(bundle, name), src)
            written.append(att_rel)
            session.add(
                DocumentAttachment(
                    document_id=doc.id,
                    role=attachment.role,
                    filename=name,
                    file_path=att_rel,
                    file_size=att_size,
                    page_count=attachment.page_count,
                )
            )
        session.commit()
    except BaseException:
        session.rollback()
        for rel in written:
            storage.delete_file(rel)
        storage.remove_dir(bundle)
        raise


def disk_name_taken(
    session: Session, parent_id: int | None, name: str, exclude_id: int | None = None
) -> bool:
    """True when a sibling folder already maps to the same directory name on disk."""
    same_parent = Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id
    target = safe_name(name)
    if target in bundle_dir_names(session, parent_id):
        return True
    return any(
        f.id != exclude_id and safe_name(f.name) == target
        for f in session.exec(select(Folder).where(same_parent))
    )
