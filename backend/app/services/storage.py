import errno
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings

log = logging.getLogger("origami.storage")

PART_SUFFIX = ".part"
OLD_LAYOUT_NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.")


def preview_name(document_id: uuid.UUID) -> str:
    return f"{document_id}.preview.pdf"


def companion_name(document_id: uuid.UUID) -> str:
    return f"{document_id}.ocr.pdf"


def _exists_error(path: Path) -> FileExistsError:
    return FileExistsError(errno.EEXIST, "Already exists", str(path))


class Storage:
    """Document tree under `root`; derived files and scan sessions in sibling directories."""

    def __init__(self, root: Path, derived_root: Path | None = None, tmp_root: Path | None = None):
        self.root = Path(root)
        self.derived_root = Path(derived_root) if derived_root else self.root.parent / "derived"
        self.tmp_root = Path(tmp_root) if tmp_root else self.root.parent / "tmp"

    # --- paths ---
    def abs_path(self, rel: str) -> Path:
        return self.root / rel

    def derived_abs(self, name: str) -> Path:
        return self.derived_root / name

    def tmp_abs(self, rel: str) -> Path:
        return self.tmp_root / rel

    # --- writes ---
    @staticmethod
    def _write_atomic(target: Path, data: bytes | BinaryIO) -> int:
        """Write `<name>.part` next to the target, then replace: readers never see half a file."""
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + PART_SUFFIX)
        try:
            with part.open("wb") as out:
                if isinstance(data, (bytes, bytearray)):
                    out.write(data)
                else:
                    shutil.copyfileobj(data, out)
            os.replace(part, target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return target.stat().st_size

    def write_file(self, rel: str, data: bytes | BinaryIO) -> tuple[str, int]:
        return rel, self._write_atomic(self.abs_path(rel), data)

    def write_derived(self, name: str, data: bytes) -> str:
        self._write_atomic(self.derived_abs(name), data)
        return name

    # --- moves ---
    def move_file(self, old_rel: str, new_rel: str) -> bool:
        """Rename inside the tree; False when the source is missing (logged, not an error)."""
        if old_rel == new_rel:
            return True
        src, dst = self.abs_path(old_rel), self.abs_path(new_rel)
        if not src.exists():
            log.warning("Document file %s is missing; nothing to move", src)
            return False
        if dst.exists():
            raise _exists_error(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        return True

    def move_dir(self, old_rel: str, new_rel: str) -> bool:
        """Rename a folder directory; a missing source creates the target instead (False)."""
        if old_rel == new_rel:
            return True
        src, dst = self.abs_path(old_rel), self.abs_path(new_rel)
        if dst.exists():
            raise _exists_error(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not src.is_dir():
            dst.mkdir()
            return False
        os.rename(src, dst)
        return True

    def make_dir(self, rel: str) -> None:
        self.abs_path(rel).mkdir(parents=True, exist_ok=True)

    def remove_dir(self, rel: str) -> None:
        try:
            self.abs_path(rel).rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("Folder directory %s not removed: not empty", rel)

    # --- deletes ---
    def delete_file(self, rel: str | None) -> None:
        if rel:
            self.abs_path(rel).unlink(missing_ok=True)

    def delete_derived(self, name: str | None) -> None:
        if name:
            self.derived_abs(name).unlink(missing_ok=True)

    def delete_document_files(
        self, file_rel: str | None, preview: str | None, document_id: uuid.UUID
    ) -> None:
        self.delete_file(file_rel)
        self.delete_derived(preview)
        self.delete_derived(companion_name(document_id))

    def remove_part_files(self, older_than_seconds: float) -> list[Path]:
        """Delete `*.part` leftovers of interrupted writes older than the given age."""
        cutoff = time.time() - older_than_seconds
        removed: list[Path] = []
        for base in (self.root, self.derived_root):
            if not base.is_dir():
                continue
            for part in base.rglob("*" + PART_SUFFIX):
                if part.is_file() and part.stat().st_mtime < cutoff:
                    part.unlink(missing_ok=True)
                    removed.append(part)
        return removed

    # --- scan sessions ---
    @property
    def tmp_scans_dir(self) -> Path:
        d = self.tmp_root / "scan_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def scan_session_dir(self, session_id: int) -> Path:
        d = self.tmp_scans_dir / str(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_scan_session_dir(self, session_id: int) -> None:
        shutil.rmtree(self.tmp_scans_dir / str(session_id), ignore_errors=True)

    # --- layout ---
    def has_old_layout(self) -> bool:
        """True while uuid-named files of the old flat layout remain in `files/`."""
        files = self.root / "files"
        return files.is_dir() and any(
            p.is_file() and OLD_LAYOUT_NAME.match(p.name) for p in files.iterdir()
        )


class OldStorageLayout(RuntimeError):
    """uuid-named files of the old flat layout are still in STORAGE_PATH/files."""


def require_new_layout(storage: Storage) -> None:
    if storage.has_old_layout():
        raise OldStorageLayout(
            f"Old storage layout found in {storage.root}; run: python -m app.cli migrate-storage"
        )


def get_storage() -> Storage:
    settings = get_settings()
    return Storage(
        settings.storage_path,
        Path(settings.derived_path) if settings.derived_path else None,
        Path(settings.tmp_path) if settings.tmp_path else None,
    )
