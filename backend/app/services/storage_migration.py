"""One-time move from the flat uuid layout (files/<uuid>.<ext>) to the folder tree."""

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sqlalchemy import func, or_, update
from sqlmodel import Session, select

from app.models import Document, ScanPage
from app.services.storage import PART_SUFFIX, Storage, companion_name, preview_name
from app.services.tree_paths import document_rel_path

log = logging.getLogger("origami.migration")

OLD_FILE = re.compile(r"^files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[^/]+$")


@dataclass
class MigrationReport:
    moved: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    leftovers: list[str] = field(default_factory=list)


@dataclass
class CheckReport:
    missing: list[str] = field(default_factory=list)
    unreferenced: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unreferenced or self.parts)


def _move_out(src: Path, dst: Path) -> None:
    """Move across roots (derived/tmp may sit on another disk)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _migrate_tmp(session: Session, storage: Storage, dry_run: bool, report: MigrationReport) -> None:
    old_sessions = storage.root / "tmp" / "scan_sessions"
    if old_sessions.is_dir():
        for child in sorted(old_sessions.iterdir()):
            dst = storage.tmp_root / "scan_sessions" / child.name
            report.moved.append((f"tmp/scan_sessions/{child.name}", f"tmp:scan_sessions/{child.name}"))
            if not dry_run and not dst.exists():
                _move_out(child, dst)
    if not dry_run:
        session.execute(
            update(ScanPage)
            .where(ScanPage.image_path.startswith("tmp/"))
            .values(image_path=func.substr(ScanPage.image_path, 5))
            .execution_options(synchronize_session=False)
        )
        session.commit()
        for d in (old_sessions, storage.root / "tmp"):
            try:
                d.rmdir()
            except OSError:
                pass


def _migrate_document(session: Session, storage: Storage, doc: Document, dry_run: bool, report: MigrationReport) -> None:
    old = doc.file_path
    if old and OLD_FILE.match(old):
        new = document_rel_path(session, storage, doc, PurePosixPath(old).suffix)
        report.moved.append((old, new))
        if not dry_run:
            if not storage.move_file(old, new):
                report.missing.append(old)
            doc.file_path = new
            session.commit()  # commit right after the move: a crash loses at most one document
    companion = f"files/{doc.id}.pdf"
    if companion != old and storage.abs_path(companion).is_file():
        report.moved.append((companion, f"derived:{companion_name(doc.id)}"))
        if not dry_run:
            _move_out(storage.abs_path(companion), storage.derived_abs(companion_name(doc.id)))
    if doc.preview_path and doc.preview_path.startswith("files/"):
        name = preview_name(doc.id)
        report.moved.append((doc.preview_path, f"derived:{name}"))
        if not dry_run:
            src = storage.abs_path(doc.preview_path)
            if src.is_file():
                _move_out(src, storage.derived_abs(name))
            doc.preview_path = name
            session.commit()


def migrate_storage(session: Session, storage: Storage, dry_run: bool = False) -> MigrationReport:
    """Idempotent and resumable: each document is moved and committed on its own."""
    report = MigrationReport()
    _migrate_tmp(session, storage, dry_run, report)
    docs = session.exec(
        select(Document)
        .where(
            or_(
                Document.file_path.op("~")(OLD_FILE.pattern),
                Document.preview_path.startswith("files/"),
            )
        )
        .order_by(Document.created_at, Document.id)
    ).all()
    for doc in docs:
        _migrate_document(session, storage, doc, dry_run, report)
    files_dir = storage.root / "files"
    if files_dir.is_dir():
        report.leftovers = sorted(p.name for p in files_dir.iterdir())
        if not dry_run and not report.leftovers:
            files_dir.rmdir()
    return report


def check_storage(session: Session, storage: Storage, fix: bool = False) -> CheckReport:
    """Compare the database with the tree; `fix` removes `*.part` leftovers."""
    report = CheckReport()
    paths = {p for p in session.exec(select(Document.file_path).where(Document.file_path.is_not(None)))}
    report.missing = sorted(p for p in paths if not storage.abs_path(p).is_file())
    if storage.root.is_dir():
        for path in sorted(storage.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(storage.root).as_posix()
            if rel.endswith(PART_SUFFIX):
                report.parts.append(rel)
                if fix:
                    path.unlink(missing_ok=True)
            elif rel not in paths:
                report.unreferenced.append(rel)
    return report
