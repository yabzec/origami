"""One-time move from the flat uuid layout (files/<uuid>.<ext>) to the folder tree."""

import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sqlalchemy import func, or_, update
from sqlmodel import Session, select

from app.models import Document, ScanPage
from app.services.storage import OLD_LAYOUT_NAME, PART_SUFFIX, Storage, companion_name, preview_name
from app.services.tree_paths import document_rel_path

JOURNAL_NAME = "storage-migration.journal"

log = logging.getLogger("origami.migration")

OLD_FILE = re.compile(r"^files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[^/]+$")


@dataclass
class MigrationReport:
    moved: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    leftovers: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)


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


def _journal_path(storage: Storage) -> Path:
    return storage.derived_root / JOURNAL_NAME


def _journal_append(storage: Storage, doc: Document, old: str, new: str) -> None:
    path = _journal_path(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"doc_id": str(doc.id), "old": old, "new": new}) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _replay_journal(session: Session, storage: Storage) -> None:
    """Finish renames whose database commit was lost to a crash."""
    path = _journal_path(storage)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            doc = session.get(Document, uuid.UUID(entry["doc_id"]))
        except (ValueError, KeyError):
            continue
        if (
            doc is not None
            and doc.file_path == entry["old"]
            and not storage.abs_path(entry["old"]).exists()
            and storage.abs_path(entry["new"]).exists()
        ):
            doc.file_path = entry["new"]
            session.commit()


def _migrate_document(session: Session, storage: Storage, doc: Document, dry_run: bool, report: MigrationReport) -> None:
    old = doc.file_path
    if old and OLD_FILE.match(old):
        new = document_rel_path(session, storage, doc, PurePosixPath(old).suffix)
        report.moved.append((old, new))
        if not dry_run:
            _journal_append(storage, doc, old, new)
            if not storage.move_file(old, new):
                report.missing.append(old)
            doc.file_path = new
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
    if not dry_run:
        session.commit()  # one commit per document, after all its files moved


def _sweep_files_dir(session: Session, storage: Storage, dry_run: bool, report: MigrationReport) -> None:
    files_dir = storage.root / "files"
    if not files_dir.is_dir():
        return
    if not dry_run:
        # companions stranded by an earlier interrupted run
        for path in sorted(files_dir.glob("*.pdf")):
            stem = path.name[: -len(".pdf")]
            try:
                doc = session.get(Document, uuid.UUID(stem))
            except ValueError:
                continue
            if doc is not None and doc.file_path != f"files/{path.name}":
                dst = storage.derived_abs(companion_name(doc.id))
                if dst.exists():
                    log.warning("%s exists; leaving %s in place", dst, path)
                    continue
                report.moved.append((f"files/{path.name}", f"derived:{dst.name}"))
                _move_out(path, dst)
        # uuid-named orphans: quarantine, never delete
        for path in sorted(files_dir.iterdir()):
            if not (path.is_file() and OLD_LAYOUT_NAME.match(path.name)):
                continue
            dst = storage.derived_abs(f"orphans/{path.name}")
            if dst.exists():
                log.warning("%s exists; leaving %s in place", dst, path)
                continue
            _move_out(path, dst)
            report.quarantined.append(path.name)
    report.leftovers = sorted(p.name for p in files_dir.iterdir())
    if not dry_run and not report.leftovers:
        files_dir.rmdir()


def migrate_storage(session: Session, storage: Storage, dry_run: bool = False) -> MigrationReport:
    """Idempotent and resumable: each document is moved and committed on its own."""
    report = MigrationReport()
    if not dry_run:
        _replay_journal(session, storage)
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
    _sweep_files_dir(session, storage, dry_run, report)
    if not dry_run and not report.missing:
        _journal_path(storage).unlink(missing_ok=True)
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
