"""Rules that turn folders and document titles into relative paths inside STORAGE_PATH."""

import re
from pathlib import Path, PurePosixPath

from sqlmodel import Session, select

from app.models import Document, DocumentAttachment, Folder

UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
MAX_NAME_BYTES = 200


def safe_name(name: str) -> str:
    """A single path segment for `name`: unsafe characters become `_`; never empty, never `.`/`..`."""
    cleaned = UNSAFE.sub("_", name).strip(" .")
    cleaned = cleaned.encode()[:MAX_NAME_BYTES].decode(errors="ignore").strip(" .")
    return cleaned or "Untitled"


def join_rel(dir_rel: str, name: str) -> str:
    return f"{dir_rel}/{name}" if dir_rel else name


def folder_rel_dir(session: Session, folder_id: int | None) -> str:
    """Directory of a folder relative to STORAGE_PATH (`""` for the root)."""
    parts: list[str] = []
    seen: set[int] = set()
    while folder_id is not None:
        if folder_id in seen:
            raise ValueError(f"Folder cycle at {folder_id}")
        seen.add(folder_id)
        folder = session.get(Folder, folder_id)
        if folder is None:
            raise ValueError(f"Folder {folder_id} not found")
        parts.append(safe_name(folder.name))
        folder_id = folder.parent_id
    return "/".join(reversed(parts))


def unique_name(dir_abs: Path, stem: str, ext: str, taken: set[str], own: str | None = None) -> str:
    """`stem.ext`, or `stem (n).ext` when the name is on disk or in `taken`; `own` always fits."""
    n = 1
    while True:
        name = f"{stem}{ext}" if n == 1 else f"{stem} ({n}){ext}"
        if name == own:
            return name
        if name not in taken and not (dir_abs / name).exists():
            return name
        n += 1


def document_rel_path(session: Session, storage, doc: Document, ext: str) -> str:
    """Target path of the document file for its current title and folder."""
    dir_rel = folder_rel_dir(session, doc.folder_id)
    same_folder = (
        Document.folder_id.is_(None) if doc.folder_id is None else Document.folder_id == doc.folder_id
    )
    rows = session.exec(
        select(Document.file_path).where(
            same_folder, Document.id != doc.id, Document.file_path.is_not(None)
        )
    ).all()
    taken = {PurePosixPath(p).name for p in rows if str(PurePosixPath(p).parent) in (dir_rel or ".",)}
    own = None
    if doc.file_path and str(PurePosixPath(doc.file_path).parent) == (dir_rel or "."):
        own = PurePosixPath(doc.file_path).name
    name = unique_name(storage.abs_path(dir_rel), safe_name(doc.title), ext.lower(), taken, own)
    return join_rel(dir_rel, name)


def has_attachments(session: Session, document_id) -> bool:
    return (
        session.exec(select(DocumentAttachment.id).where(DocumentAttachment.document_id == document_id)).first()
        is not None
    )


def bundle_dir_names(session: Session, folder_id: int | None, exclude_id=None) -> set[str]:
    """Disk names of the bundle directories (documents with attachments) directly inside a folder."""
    same_folder = Document.folder_id.is_(None) if folder_id is None else Document.folder_id == folder_id
    query = select(Document.file_path).where(
        same_folder,
        Document.file_path.is_not(None),
        Document.id.in_(select(DocumentAttachment.document_id)),
    )
    if exclude_id is not None:
        query = query.where(Document.id != exclude_id)
    return {PurePosixPath(p).parent.name for p in session.exec(query)}


def bundle_rel_dir(session: Session, storage, doc: Document) -> str:
    """Target bundle directory of a document with attachments, for its current title and folder."""
    dir_rel = folder_rel_dir(session, doc.folder_id)
    same_parent = Folder.parent_id.is_(None) if doc.folder_id is None else Folder.parent_id == doc.folder_id
    taken = bundle_dir_names(session, doc.folder_id, exclude_id=doc.id)
    taken |= {safe_name(f.name) for f in session.exec(select(Folder).where(same_parent))}
    own = None
    if doc.file_path and has_attachments(session, doc.id):
        current = PurePosixPath(doc.file_path).parent
        if str(current.parent) == (dir_rel or "."):
            own = current.name
    name = unique_name(storage.abs_path(dir_rel), safe_name(doc.title), "", taken, own)
    return join_rel(dir_rel, name)
