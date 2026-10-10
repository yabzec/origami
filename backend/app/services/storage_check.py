"""Compare the documents in the database with the files in the storage tree."""

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models import Document
from app.services.storage import PART_SUFFIX, Storage


@dataclass
class CheckReport:
    missing: list[str] = field(default_factory=list)
    unreferenced: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unreferenced or self.parts)


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
