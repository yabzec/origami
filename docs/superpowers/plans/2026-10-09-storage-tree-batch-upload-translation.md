# Storage Tree, Batch Upload and Translation Language Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store documents as real files in a tree that mirrors the explorer, upload many files or whole folders in one action, and give each document its own translation target language with defaults from `.env`.

**Architecture:** A pure path service (`tree_paths.py`) turns folders and titles into safe relative paths. A small sync layer (`tree_sync.py`) moves files on disk inside the same database transaction that changes titles or folders, and moves them back if the commit fails. `Storage` gets three roots: the tree (`STORAGE_PATH`), derived files and temporary scan files (siblings). Batch upload is a frontend queue over the existing single-file upload endpoint plus a new `ensure-path` folder endpoint. Translation reads a new `documents.translation_language` column instead of `PRIMARY_LANGUAGE`.

**Tech Stack:** FastAPI, SQLModel, PostgreSQL, Alembic, pytest (real Postgres); React 19, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-storage-tree-batch-upload-translation-design.md`

## Global Constraints

- `STORAGE_PATH` holds only the document tree. No app files, no hidden files inside it (except transient `*.part` files during a write).
- `DERIVED_PATH` empty = `<STORAGE_PATH>/../derived`. `TMP_PATH` empty = `<STORAGE_PATH>/../tmp`.
- Derived file names: `<uuid>.preview.pdf` (office preview), `<uuid>.ocr.pdf` (image OCR companion).
- `Document.file_path` is relative to `STORAGE_PATH`; `Document.preview_path` is relative to `DERIVED_PATH`; `ScanPage.image_path` is relative to `TMP_PATH`.
- Unsafe characters on disk: `/ \ : * ? " < > |` and control characters become `_`; names are trimmed of leading/trailing dots and spaces, capped at 200 UTF-8 bytes, empty becomes `Untitled`.
- Collision suffix: `Name.ext`, `Name (2).ext`, `Name (3).ext`, …
- Error codes: `storage_conflict` (409), `storage_error` (500), `duplicate_folder` (409), `unknown_translation_language` (422), `nothing_to_translate` (409).
- New env vars: `DERIVED_PATH`, `TMP_PATH`, `DEFAULT_TRANSLATION_LANGUAGE` (ISO 639-1; empty = `PRIMARY_LANGUAGE`). `PRIMARY_LANGUAGE` now only sets the AI summary language.
- Batch upload: three uploads in parallel; title = file name without extension; document date = `File.lastModified` as local date.
- Backend tests use real Postgres (`docker compose up -d db`). Run from `backend/`: `uv run pytest …`. Frontend tests run from `frontend/`: `npx vitest run …`.
- Code comments, commit messages and docs are plain English. Commit messages use Conventional Commits.

## Review Focus

1. Two sibling folders whose names sanitize to the same disk name (`a/b` and `a_b`) must be refused with 409 `duplicate_folder`, never merged into one directory. Test in Task 2 and Task 6.
2. Bulk move of two documents with the same title into one folder must give `X.pdf` and `X (2).pdf`, both on disk. Test in Task 5.
3. A title made only of unsafe characters or dots (`../..`) must give `Untitled.<ext>` on disk inside the folder, never a path outside `STORAGE_PATH`; the app title stays as typed. Test in Task 2 and Task 5.
4. Moving a folder where the target directory already exists on disk (a stray directory not in the database) must answer 409 `storage_conflict` and leave database and disk unchanged. Test in Task 6.
5. In a batch, a failed `ensure-path` for one subfolder must fail only that subfolder's files; **Retry failed** must resolve the folder again. Test in Task 11.

---

## File Structure

Backend (`backend/`):
- `app/config.py` — new settings and `get_default_translation_language()`.
- `app/services/storage.py` — rewritten: three roots, atomic writes, moves, derived and tmp files, old-layout detection.
- `app/services/tree_paths.py` (new) — pure path rules: `safe_name`, `folder_rel_dir`, `unique_name`, `document_rel_path`.
- `app/services/tree_sync.py` (new) — `MoveLog`, `disk_transaction`, `lock_documents`, `relocate_document`, `write_document_file`, `disk_name_taken`.
- `app/api/storage_errors.py` (new) — maps `OSError` to API errors.
- `app/services/storage_migration.py` (new) — `migrate_storage`, `check_storage`.
- `app/api/uploads.py`, `app/api/documents.py`, `app/api/folders.py`, `app/api/files.py`, `app/api/scan.py`, `app/api/ocr.py`, `app/worker/pipeline.py`, `app/worker/__main__.py`, `app/main.py`, `app/cli.py`, `app/models/document.py`, `app/services/ocr_language_names.py`, `app/services/llm.py` — modified.
- `alembic/versions/b1d7e4f20a93_document_translation_language.py` (new).

Frontend (`frontend/src/`):
- `lib/processing.ts`, `lib/upload.ts`, `lib/translation.ts`, `lib/types.ts`, `lib/api.ts` — modified.
- `lib/batchUpload.ts` (new) — batch planning and the upload queue.
- `lib/dropEntries.ts` (new) — files and folders from inputs and drops.
- `hooks/useBatchUpload.ts` (new), `components/BatchUploadDialog.tsx` (new).
- `components/ProcessingOptions.tsx`, `components/UploadDialog.tsx`, `pages/BrowsePage.tsx`, `pages/ScanPage.tsx`, `pages/DocumentPage.tsx` — modified.

---

### Task 1: Settings and storage primitives

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/services/storage.py`
- Modify: `backend/tests/conftest.py` (storage fixture, test `STORAGE_PATH`)
- Modify: `backend/tests/test_pipeline.py:12-16` (fixture)
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- Produces:
  - `Settings.derived_path: str`, `Settings.tmp_path: str`, `Settings.default_translation_language: str`
  - `get_default_translation_language() -> str` in `app/config.py`
  - `Storage(root, derived_root=None, tmp_root=None)` with attributes `root`, `derived_root`, `tmp_root`
  - `Storage.abs_path(rel) -> Path`, `derived_abs(name) -> Path`, `tmp_abs(rel) -> Path`
  - `Storage.write_file(rel, data: bytes | BinaryIO) -> tuple[str, int]`
  - `Storage.write_derived(name, data: bytes) -> str`
  - `Storage.move_file(old_rel, new_rel) -> bool` (False = source missing), raises `FileExistsError` when the target exists
  - `Storage.move_dir(old_rel, new_rel) -> bool`, raises `FileExistsError` when the target exists
  - `Storage.make_dir(rel)`, `remove_dir(rel)`, `delete_file(rel)`, `delete_derived(name)`
  - `Storage.delete_document_files(file_rel, preview_name, doc_id)`
  - `Storage.remove_part_files(older_than_seconds: float) -> list[Path]`
  - `Storage.has_old_layout() -> bool`
  - module functions `preview_name(doc_id) -> str`, `companion_name(doc_id) -> str`, `require_new_layout(storage) -> None` (raises `OldStorageLayout`, a `RuntimeError`)
- The old methods `store_file`, `store_fileobj`, `store_preview`, `delete_document_file`, `files_dir` stay until Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_storage.py`:

```python
import os
import time

import pytest

from app.services.storage import OldStorageLayout, companion_name, preview_name, require_new_layout


def make_storage(tmp_path):
    return Storage(tmp_path / "storage")


def test_roots_default_to_siblings(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.root == tmp_path / "storage"
    assert storage.derived_root == tmp_path / "derived"
    assert storage.tmp_root == tmp_path / "tmp"


def test_explicit_roots(tmp_path):
    storage = Storage(tmp_path / "s", tmp_path / "d2", tmp_path / "t2")
    assert storage.derived_root == tmp_path / "d2"
    assert storage.tmp_root == tmp_path / "t2"


def test_write_file_creates_dirs_and_leaves_no_part(tmp_path):
    storage = make_storage(tmp_path)
    rel, size = storage.write_file("Home/Bills/Invoice.pdf", b"%PDF-1")
    assert rel == "Home/Bills/Invoice.pdf"
    assert size == 6
    assert storage.abs_path(rel).read_bytes() == b"%PDF-1"
    assert not list(storage.root.rglob("*.part"))


def test_write_file_streams_fileobj(tmp_path):
    storage = make_storage(tmp_path)
    rel, size = storage.write_file("a.mp4", io.BytesIO(b"0123456789"))
    assert size == 10
    assert storage.abs_path(rel).read_bytes() == b"0123456789"


def test_write_file_replaces_existing(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("a.pdf", b"old")
    storage.write_file("a.pdf", b"new")
    assert storage.abs_path("a.pdf").read_bytes() == b"new"


def test_write_derived(tmp_path):
    storage = make_storage(tmp_path)
    doc_id = uuid.uuid4()
    name = storage.write_derived(preview_name(doc_id), b"%PDF")
    assert name == f"{doc_id}.preview.pdf"
    assert storage.derived_abs(name).read_bytes() == b"%PDF"
    assert companion_name(doc_id) == f"{doc_id}.ocr.pdf"


def test_move_file(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("A/x.pdf", b"1")
    assert storage.move_file("A/x.pdf", "B/C/y.pdf") is True
    assert storage.abs_path("B/C/y.pdf").read_bytes() == b"1"
    assert not storage.abs_path("A/x.pdf").exists()


def test_move_file_missing_source_returns_false(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.move_file("nope.pdf", "other.pdf") is False


def test_move_file_refuses_existing_target(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("a.pdf", b"1")
    storage.write_file("b.pdf", b"2")
    with pytest.raises(FileExistsError):
        storage.move_file("a.pdf", "b.pdf")
    assert storage.abs_path("b.pdf").read_bytes() == b"2"


def test_move_dir(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("A/B/x.pdf", b"1")
    assert storage.move_dir("A/B", "C/D") is True
    assert storage.abs_path("C/D/x.pdf").exists()
    assert not storage.abs_path("A/B").exists()


def test_move_dir_missing_source_creates_target(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.move_dir("Gone", "New") is False
    assert storage.abs_path("New").is_dir()


def test_move_dir_refuses_existing_target(tmp_path):
    storage = make_storage(tmp_path)
    storage.make_dir("A")
    storage.make_dir("B")
    with pytest.raises(FileExistsError):
        storage.move_dir("A", "B")


def test_remove_dir_keeps_non_empty(tmp_path):
    storage = make_storage(tmp_path)
    storage.make_dir("Empty")
    storage.write_file("Full/x.pdf", b"1")
    storage.remove_dir("Empty")
    storage.remove_dir("Full")
    storage.remove_dir("Missing")
    assert not storage.abs_path("Empty").exists()
    assert storage.abs_path("Full/x.pdf").exists()


def test_delete_document_files(tmp_path):
    storage = make_storage(tmp_path)
    doc_id = uuid.uuid4()
    storage.write_file("a.png", b"1")
    storage.write_derived(preview_name(doc_id), b"2")
    storage.write_derived(companion_name(doc_id), b"3")
    storage.delete_document_files("a.png", preview_name(doc_id), doc_id)
    assert not storage.abs_path("a.png").exists()
    assert not list(storage.derived_root.iterdir())
    storage.delete_document_files(None, None, doc_id)  # idempotent


def test_remove_part_files_only_old(tmp_path):
    storage = make_storage(tmp_path)
    storage.make_dir("A")
    old = storage.abs_path("A/x.pdf.part")
    old.write_bytes(b"")
    past = time.time() - 7200
    os.utime(old, (past, past))
    fresh = storage.abs_path("y.pdf.part")
    fresh.write_bytes(b"")
    assert storage.remove_part_files(3600) == [old]
    assert fresh.exists()


def test_old_layout_detection(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.has_old_layout() is False
    require_new_layout(storage)
    storage.write_file("files/notes.txt", b"user folder named files")
    assert storage.has_old_layout() is False
    storage.write_file(f"files/{uuid.uuid4()}.pdf", b"%PDF")
    assert storage.has_old_layout() is True
    with pytest.raises(OldStorageLayout, match="migrate-storage"):
        require_new_layout(storage)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ImportError: cannot import name 'companion_name'`

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`, add inside `Settings` after `storage_path`:

```python
    derived_path: str = ""  # empty → <storage_path>/../derived (office previews, OCR companions)
    tmp_path: str = ""  # empty → <storage_path>/../tmp (scan sessions)
```

and after `primary_language`:

```python
    default_translation_language: str = ""  # ISO 639-1; empty → primary_language
```

Append at the end of the file:

```python
def get_default_translation_language() -> str:
    """Configured default translation target (not checked against installed languages)."""
    settings = get_settings()
    return settings.default_translation_language.strip() or settings.primary_language
```

- [ ] **Step 4: Rewrite `backend/app/services/storage.py`**

Keep the old methods for now; add the rest. Full file:

```python
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

    # --- legacy flat layout (removed in a later task) ---
    @property
    def files_dir(self) -> Path:
        d = self.root / "files"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def tmp_scans_dir(self) -> Path:
        d = self.root / "tmp" / "scan_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def store_file(self, document_id: uuid.UUID, ext: str, data: bytes) -> tuple[str, int]:
        name = f"{document_id}{ext}"
        path = self.files_dir / name
        path.write_bytes(data)
        return f"files/{name}", len(data)

    def store_fileobj(self, document_id: uuid.UUID, ext: str, fileobj) -> tuple[str, int]:
        name = f"{document_id}{ext}"
        path = self.files_dir / name
        with path.open("wb") as out:
            shutil.copyfileobj(fileobj, out)
        return f"files/{name}", path.stat().st_size

    def store_preview(self, document_id: uuid.UUID, data: bytes) -> str:
        name = f"{document_id}.preview.pdf"
        (self.files_dir / name).write_bytes(data)
        return f"files/{name}"

    def delete_document_file(self, rel: str | None) -> None:
        if rel:
            self.abs_path(rel).unlink(missing_ok=True)

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
```

- [ ] **Step 5: Isolate test storage**

In `backend/tests/conftest.py`, add after the `os.environ.setdefault("JWT_SECRET", …)` line:

```python
import tempfile  # noqa: E402

# Never let a test (or the startup layout check) touch the developer's real STORAGE_PATH.
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="origami-test-storage-")
```

Change the `storage` fixture body line `s = Storage(tmp_path)` to:

```python
    s = Storage(tmp_path / "storage")  # derived/ and tmp/ become siblings inside tmp_path
```

In `backend/tests/test_pipeline.py`, change the `pipeline_storage` fixture line `s = Storage(tmp_path)` to `s = Storage(tmp_path / "storage")`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (all old and new tests)

Run: `uv run pytest -q`
Expected: PASS (whole suite still green)

- [ ] **Step 7: Commit**

```bash
git add backend/app/config.py backend/app/services/storage.py backend/tests/conftest.py backend/tests/test_pipeline.py backend/tests/test_storage.py
git commit -m "feat: storage roots, atomic writes and file moves"
```

---

### Task 2: Path service

**Files:**
- Create: `backend/app/services/tree_paths.py`
- Test: `backend/tests/test_tree_paths.py`

**Interfaces:**
- Consumes: `Storage.abs_path` (Task 1).
- Produces:
  - `safe_name(name: str) -> str`
  - `folder_rel_dir(session, folder_id: int | None) -> str` (`""` for the root)
  - `join_rel(dir_rel: str, name: str) -> str`
  - `unique_name(dir_abs: Path, stem: str, ext: str, taken: set[str], own: str | None = None) -> str`
  - `document_rel_path(session, storage, doc: Document, ext: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tree_paths.py`:

```python
from app.models import DocType, Document, Folder
from app.services.storage import Storage
from app.services.tree_paths import (
    document_rel_path,
    folder_rel_dir,
    safe_name,
    unique_name,
)


def test_safe_name_replaces_unsafe_characters():
    assert safe_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert safe_name("tab\there") == "tab_here"


def test_safe_name_trims_dots_and_spaces():
    assert safe_name("  .hidden. ") == "hidden"
    assert safe_name("../..") == "_"
    assert safe_name("...") == "Untitled"
    assert safe_name("") == "Untitled"


def test_safe_name_caps_bytes_without_breaking_characters():
    name = safe_name("è" * 150)  # 300 bytes in UTF-8
    assert len(name.encode()) <= 200
    assert name == "è" * 100


def test_unique_name(tmp_path):
    (tmp_path / "Invoice.pdf").write_bytes(b"")
    assert unique_name(tmp_path, "Invoice", ".pdf", set()) == "Invoice (2).pdf"
    assert unique_name(tmp_path, "Invoice", ".pdf", {"Invoice (2).pdf"}) == "Invoice (3).pdf"
    assert unique_name(tmp_path, "Invoice", ".pdf", set(), own="Invoice.pdf") == "Invoice.pdf"
    assert unique_name(tmp_path / "missing", "Other", ".pdf", set()) == "Other.pdf"


def make_folder(session, name, parent_id=None):
    folder = Folder(name=name, parent_id=parent_id)
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return folder


def make_doc(session, title, folder_id=None, file_path=None):
    doc = Document(title=title, doc_type=DocType.pdf, folder_id=folder_id, file_path=file_path)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_folder_rel_dir(session):
    home = make_folder(session, "Home")
    bills = make_folder(session, "Bills: 2025", home.id)
    assert folder_rel_dir(session, None) == ""
    assert folder_rel_dir(session, home.id) == "Home"
    assert folder_rel_dir(session, bills.id) == "Home/Bills_ 2025"


def test_document_rel_path_root_and_folder(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    home = make_folder(session, "Home")
    assert document_rel_path(session, storage, make_doc(session, "Note"), ".txt") == "Note.txt"
    doc = make_doc(session, "Invoice", home.id)
    assert document_rel_path(session, storage, doc, ".pdf") == "Home/Invoice.pdf"


def test_document_rel_path_avoids_other_documents(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    make_doc(session, "Invoice", file_path="Invoice.pdf")  # in the database, not on disk
    doc = make_doc(session, "Invoice")
    assert document_rel_path(session, storage, doc, ".pdf") == "Invoice (2).pdf"


def test_document_rel_path_keeps_own_name(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    storage.write_file("Invoice.pdf", b"1")
    doc = make_doc(session, "Invoice", file_path="Invoice.pdf")
    assert document_rel_path(session, storage, doc, ".pdf") == "Invoice.pdf"


def test_document_rel_path_unsafe_title_stays_inside_folder(session, tmp_path):
    storage = Storage(tmp_path / "storage")
    home = make_folder(session, "Home")
    doc = make_doc(session, "...", home.id)
    assert document_rel_path(session, storage, doc, ".pdf") == "Home/Untitled.pdf"
    doc2 = make_doc(session, "../../etc/passwd", home.id)
    assert document_rel_path(session, storage, doc2, ".pdf") == "Home/_.._etc_passwd.pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tree_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.tree_paths'`

- [ ] **Step 3: Implement `backend/app/services/tree_paths.py`**

```python
"""Rules that turn folders and document titles into relative paths inside STORAGE_PATH."""

import re
from pathlib import Path, PurePosixPath

from sqlmodel import Session, select

from app.models import Document, Folder

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tree_paths.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tree_paths.py backend/tests/test_tree_paths.py
git commit -m "feat: path rules for the storage tree"
```

---

### Task 3: Disk transactions and document file writes

**Files:**
- Create: `backend/app/services/tree_sync.py`
- Create: `backend/app/api/storage_errors.py`
- Test: `backend/tests/test_tree_sync.py`

**Interfaces:**
- Consumes: `Storage` (Task 1), `document_rel_path`, `safe_name` (Task 2).
- Produces:
  - `class MoveLog` with `move_file(old, new)`, `move_dir(old, new)`, `undo()`
  - `disk_transaction(session, storage)` context manager yielding a `MoveLog`; commits at the end, rolls back and undoes moves on any exception
  - `lock_documents(session, ids) -> list[Document]` (row locks, fresh values)
  - `relocate_document(session, storage, moves, doc) -> None`
  - `write_document_file(session, storage, doc, ext, data) -> None` (locks the row, writes, commits)
  - `disk_name_taken(session, parent_id, name, exclude_id=None) -> bool`
  - `storage_errors()` context manager in `app/api/storage_errors.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tree_sync.py`:

```python
import pytest
from fastapi import HTTPException

from app.api.storage_errors import storage_errors
from app.models import DocType, Document, Folder
from app.services.storage import Storage
from app.services.tree_sync import (
    disk_name_taken,
    disk_transaction,
    lock_documents,
    relocate_document,
    write_document_file,
)


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "storage")


def make_doc(session, title="Invoice", **kw):
    doc = Document(title=title, doc_type=kw.pop("doc_type", DocType.pdf), **kw)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_write_document_file_places_by_title(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".PDF", b"%PDF")
    session.refresh(doc)
    assert doc.file_path == "Invoice.pdf"
    assert doc.file_size == 4
    assert store.abs_path("Invoice.pdf").read_bytes() == b"%PDF"


def test_write_document_file_same_ext_overwrites_in_place(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"old")
    write_document_file(session, store, doc, ".pdf", b"new!")
    session.refresh(doc)
    assert doc.file_path == "Invoice.pdf"
    assert store.abs_path("Invoice.pdf").read_bytes() == b"new!"


def test_write_document_file_new_ext_removes_old_file(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".png", b"png")
    write_document_file(session, store, doc, ".pdf", b"%PDF")
    session.refresh(doc)
    assert doc.file_path == "Invoice.pdf"
    assert not store.abs_path("Invoice.png").exists()


def test_disk_transaction_commits_moves(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"1")
    with disk_transaction(session, store) as moves:
        [locked] = lock_documents(session, [doc.id])
        locked.title = "Renamed"
        relocate_document(session, store, moves, locked)
    session.refresh(doc)
    assert doc.file_path == "Renamed.pdf"
    assert store.abs_path("Renamed.pdf").exists()


def test_disk_transaction_undoes_moves_on_error(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"1")
    with pytest.raises(RuntimeError):
        with disk_transaction(session, store) as moves:
            [locked] = lock_documents(session, [doc.id])
            locked.title = "Renamed"
            relocate_document(session, store, moves, locked)
            raise RuntimeError("commit would fail")
    session.refresh(doc)
    assert doc.title == "Invoice"
    assert doc.file_path == "Invoice.pdf"
    assert store.abs_path("Invoice.pdf").exists()
    assert not store.abs_path("Renamed.pdf").exists()


def test_relocate_without_file_only_skips(session, store):
    doc = make_doc(session)
    with disk_transaction(session, store) as moves:
        relocate_document(session, store, moves, doc)
    assert doc.file_path is None


def test_disk_name_taken(session):
    home = Folder(name="Home")
    session.add(home)
    session.commit()
    session.add(Folder(name="a/b", parent_id=home.id))
    session.commit()
    assert disk_name_taken(session, home.id, "a_b") is True
    assert disk_name_taken(session, home.id, "a:b") is True
    assert disk_name_taken(session, home.id, "other") is False
    assert disk_name_taken(session, None, "Home") is True


def test_storage_errors_mapping(tmp_path):
    with pytest.raises(HTTPException) as conflict:
        with storage_errors():
            raise FileExistsError(17, "Already exists", str(tmp_path / "x"))
    assert conflict.value.status_code == 409
    assert conflict.value.detail["error"]["code"] == "storage_conflict"
    with pytest.raises(HTTPException) as broken:
        with storage_errors():
            raise PermissionError(13, "Permission denied", str(tmp_path / "y"))
    assert broken.value.status_code == 500
    assert broken.value.detail["error"]["code"] == "storage_error"
    assert "y" in broken.value.detail["error"]["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tree_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.storage_errors'`

- [ ] **Step 3: Implement `backend/app/api/storage_errors.py`**

```python
from contextlib import contextmanager

from app.api.deps import api_error


@contextmanager
def storage_errors():
    """Turn filesystem errors from the storage tree into API errors."""
    try:
        yield
    except FileExistsError as exc:
        raise api_error(
            409, "storage_conflict", f"A file or folder already exists at {exc.filename or exc}"
        ) from exc
    except OSError as exc:
        raise api_error(
            500, "storage_error", f"Storage error at {exc.filename}: {exc.strerror or exc}"
        ) from exc
```

- [ ] **Step 4: Implement `backend/app/services/tree_sync.py`**

```python
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
    rel, size = storage.write_file(rel, data)
    if old and old != rel:
        storage.delete_file(old)
    doc.file_path = rel
    doc.file_size = size
    session.commit()


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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tree_sync.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/tree_sync.py backend/app/api/storage_errors.py backend/tests/test_tree_sync.py
git commit -m "feat: disk transactions for storage tree changes"
```

---

### Task 4: Upload and worker write to the tree; derived files

**Files:**
- Modify: `backend/app/api/uploads.py:121-130`
- Modify: `backend/app/worker/pipeline.py` (`get_pipeline_storage`, `_extract_content`, `_extract_office`, `_reocr_pdf`, `_extract_scan`)
- Modify: `backend/app/api/files.py:32-33`
- Modify: `backend/app/api/documents.py` (`_delete_documents`, `delete_document`, `bulk_delete`)
- Modify: `backend/app/services/storage.py` (remove legacy methods except `tmp_scans_dir`)
- Modify tests: `tests/test_storage.py`, `tests/test_pipeline.py`, `tests/test_reprocess.py`, `tests/test_processing_flags.py`, `tests/test_bulk.py`, `tests/test_files_api.py`, `tests/test_documents.py`, `tests/test_scan_compile.py`, `tests/test_uploads.py`

**Interfaces:**
- Consumes: `write_document_file` (Task 3), `Storage.write_derived`, `derived_abs`, `delete_document_files`, `preview_name`, `companion_name`, `get_storage` (Task 1).
- Produces: every document file lives at its tree path; `preview_path` is a bare derived name.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_uploads.py`:

```python
def test_upload_places_file_in_folder_tree(auth_client, session, storage):
    from app.models import Folder

    home = Folder(name="Home")
    session.add(home)
    session.commit()
    bills = Folder(name="Bills", parent_id=home.id)
    session.add(bills)
    session.commit()

    first = auth_client.post(
        "/api/documents/upload",
        files={"file": ("ACME.pdf", b"%PDF-1", "application/pdf")},
        data={"folder_id": str(bills.id), "title": "Invoice"},
    ).json()
    second = auth_client.post(
        "/api/documents/upload",
        files={"file": ("other.PDF", b"%PDF-2", "application/pdf")},
        data={"folder_id": str(bills.id), "title": "Invoice"},
    ).json()
    assert first["file_path"] == "Home/Bills/Invoice.pdf"
    assert second["file_path"] == "Home/Bills/Invoice (2).pdf"
    assert storage.abs_path("Home/Bills/Invoice (2).pdf").read_bytes() == b"%PDF-2"
```

In `backend/tests/test_pipeline.py`, add:

```python
def test_image_ocr_companion_goes_to_derived(session, pipeline_storage, llm_stub, tmp_path):
    from app.services.storage import companion_name

    img = make_text_image(tmp_path / "scan.png")
    doc = make_doc(session, doc_type=DocType.image, title="Foto")
    pipeline_storage.write_file("Foto.png", img.read_bytes())
    doc.file_path = "Foto.png"
    session.commit()
    run(session, doc)
    assert pipeline_storage.derived_abs(companion_name(doc.id)).exists()
    assert sorted(p.name for p in pipeline_storage.root.iterdir()) == ["Foto.png"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_uploads.py::test_upload_places_file_in_folder_tree tests/test_pipeline.py::test_image_ocr_companion_goes_to_derived -v`
Expected: FAIL (`file_path` is `files/<uuid>.pdf`; companion missing in derived)

- [ ] **Step 3: Upload writes to the tree**

In `backend/app/api/uploads.py`, add imports:

```python
from app.api.storage_errors import storage_errors
from app.services.tree_sync import write_document_file
```

Replace:

```python
    rel, size = storage.store_fileobj(doc.id, ext, file.file)
    doc.file_path = rel
    doc.file_size = size
    session.commit()
```

with:

```python
    with storage_errors():
        write_document_file(session, storage, doc, ext, file.file)
```

- [ ] **Step 4: Worker writes to the tree and to derived**

In `backend/app/worker/pipeline.py`:

Imports — replace `from app.services.storage import Storage` with:

```python
from app.services.storage import Storage, companion_name, get_storage, preview_name
from app.services.tree_sync import write_document_file
```

`get_pipeline_storage` body:

```python
    return get_storage()
```

In `_extract_content`, image branch, replace `storage.store_file(doc.id, ".pdf", pdf_bytes)  # companion searchable PDF` with:

```python
        storage.write_derived(companion_name(doc.id), pdf_bytes)  # companion searchable PDF
```

PDF branch, replace:

```python
            pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
            rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
            doc.file_path = rel
            doc.file_size = size
            doc.ocr_applied = True
```

with:

```python
            pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
            write_document_file(session, storage, doc, ".pdf", pdf_bytes)
            doc.ocr_applied = True
```

In `_extract_office`, replace `preview = storage.abs_path(doc.preview_path) if doc.preview_path else None` with:

```python
    preview = storage.derived_abs(doc.preview_path) if doc.preview_path else None
```

and replace:

```python
        doc.preview_path = storage.store_preview(doc.id, pdf_bytes)
        preview = storage.abs_path(doc.preview_path)
```

with:

```python
        doc.preview_path = storage.write_derived(preview_name(doc.id), pdf_bytes)
        preview = storage.derived_abs(doc.preview_path)
```

In `_reocr_pdf`, replace:

```python
        rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
        doc.file_path = rel
        doc.file_size = size
        doc.ocr_applied = True
```

with:

```python
        write_document_file(session, storage, doc, ".pdf", pdf_bytes)
        doc.ocr_applied = True
```

In `_extract_scan`, replace:

```python
        rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
        doc.file_path = rel
        doc.file_size = size
        doc.ocr_applied = doc.ocr_enabled
```

with:

```python
        write_document_file(session, storage, doc, ".pdf", pdf_bytes)
        doc.ocr_applied = doc.ocr_enabled
```

- [ ] **Step 5: Files API reads the preview from derived**

In `backend/app/api/files.py`, replace `preview_file = storage.abs_path(doc.preview_path)` with:

```python
        preview_file = storage.derived_abs(doc.preview_path)
```

- [ ] **Step 6: Deletes remove tree and derived files**

In `backend/app/api/documents.py`, replace `_delete_documents`:

```python
def _delete_documents(session: Session, docs: list[Document]) -> list[tuple]:
    """Cancel queued jobs and delete rows (no commit); returns the files to remove after commit."""
    files: list[tuple] = []
    for doc in docs:
        files.append((doc.file_path, doc.preview_path, doc.id))
        _cancel_queued_jobs(session, doc)
        session.delete(doc)  # chunks, document_tags and translation segments cascade via FK
    return files
```

In `bulk_delete` and `delete_document`, replace:

```python
    rel_paths = _delete_documents(session, docs)
    session.commit()
    for rel in rel_paths:
        storage.delete_document_file(rel)
```

(and the single-document variant with `[doc]`) with:

```python
    files = _delete_documents(session, docs)
    session.commit()
    for file_rel, preview, doc_id in files:
        storage.delete_document_files(file_rel, preview, doc_id)
```

(`_delete_documents(session, [doc])` in `delete_document`.)

- [ ] **Step 7: Remove the legacy storage methods**

In `backend/app/services/storage.py`, delete the `# --- legacy flat layout ---` block **except** the `tmp_scans_dir` property (Task 7 moves it). Delete: `files_dir`, `store_file`, `store_fileobj`, `store_preview`, `delete_document_file`. Move `tmp_scans_dir` under the `# --- scan sessions ---` heading.

In `backend/tests/test_storage.py`, delete `test_store_and_delete_file`, `test_store_fileobj_streams_and_sizes` and `test_store_preview_writes_pdf_next_to_original`.

- [ ] **Step 8: Update the other tests to the new storage API**

Run from `backend/`:

```bash
sed -i -E \
  -e 's/\.store_file\(([a-z_]+)\.id, "(\.[a-z0-9]+)", /.write_file(f"{\1.id}\2", /' \
  -e 's/\.store_file\(([a-z_]+)\.id, ext, /.write_file(f"{\1.id}{ext}", /' \
  -e 's/\.store_preview\(([a-z_]+)\.id, /.write_derived(f"{\1.id}.preview.pdf", /' \
  tests/*.py
grep -n "store_file\|store_preview\|store_fileobj\|files/" tests/*.py
```

Fix each remaining hit by hand:
- `tests/test_pipeline.py` companion checks: `pipeline_storage.abs_path(f"files/{doc.id}.pdf")` → `pipeline_storage.derived_abs(f"{doc.id}.ocr.pdf")` (both the `exists()` and the `not … exists()` assertion).
- `tests/test_pipeline.py` office preview: `assert doc.preview_path == f"files/{doc.id}.preview.pdf"` → `assert doc.preview_path == f"{doc.id}.preview.pdf"`; any `abs_path(doc.preview_path)` → `derived_abs(doc.preview_path)`.
- `tests/test_scan_compile.py:64`: `assert doc.file_path == f"files/{doc.id}.pdf"` → `assert doc.file_path == "Documento.pdf"` (the title used in that test).
- `tests/test_documents.py` delete test with a preview: assertions on `storage.abs_path(doc.preview_path)` → `storage.derived_abs(preview)` where `preview` is the stored name.
- Tests that check a PDF re-OCR replaced the file now see the file at the same `rel` path; `rel` stays valid because `write_document_file` keeps the path when the extension is unchanged.

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. Any failure is a leftover path assertion from Step 8: fix it the same way.

- [ ] **Step 10: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: write uploads and worker output to the storage tree"
```

---

### Task 5: Document rename and move follow on disk

**Files:**
- Modify: `backend/app/api/documents.py` (`update_document`, `bulk_move`)
- Test: `backend/tests/test_documents.py`, `backend/tests/test_bulk.py`

**Interfaces:**
- Consumes: `disk_transaction`, `lock_documents`, `relocate_document` (Task 3), `storage_errors` (Task 3).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_documents.py`:

```python
def _doc_with_file(session, storage, title, folder_id=None):
    from app.services.tree_sync import write_document_file

    doc = make_document(session)
    doc.title = title
    doc.folder_id = folder_id
    session.commit()
    write_document_file(session, storage, doc, ".pdf", b"%PDF")
    session.refresh(doc)
    return doc


def test_title_edit_renames_file(auth_client, session, storage):
    doc = _doc_with_file(session, storage, "Old")
    body = auth_client.patch(f"/api/documents/{doc.id}", json={"title": "New"}).json()
    assert body["file_path"] == "New.pdf"
    assert storage.abs_path("New.pdf").exists()
    assert not storage.abs_path("Old.pdf").exists()


def test_unsafe_title_stays_inside_storage(auth_client, session, storage):
    doc = _doc_with_file(session, storage, "Old")
    body = auth_client.patch(f"/api/documents/{doc.id}", json={"title": "../.."}).json()
    assert body["title"] == "../.."
    assert body["file_path"] == "_.pdf"
    assert storage.abs_path("_.pdf").exists()


def test_folder_change_moves_file(auth_client, session, storage):
    from app.models import Folder

    folder = Folder(name="Archive")
    session.add(folder)
    session.commit()
    doc = _doc_with_file(session, storage, "Note")
    body = auth_client.patch(f"/api/documents/{doc.id}", json={"folder_id": folder.id}).json()
    assert body["file_path"] == "Archive/Note.pdf"
    assert storage.abs_path("Archive/Note.pdf").exists()


def test_failed_patch_keeps_file(auth_client, session, storage):
    doc = _doc_with_file(session, storage, "Old")
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"title": "New", "tag_ids": [999999]})
    assert resp.status_code == 404
    assert storage.abs_path("Old.pdf").exists()
    assert not storage.abs_path("New.pdf").exists()


def test_rename_conflict_on_disk_is_409(auth_client, session, storage, monkeypatch):
    doc = _doc_with_file(session, storage, "Old")

    def refuse(old, new):
        raise FileExistsError(17, "Already exists", str(storage.abs_path(new)))

    monkeypatch.setattr(storage, "move_file", refuse)
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"title": "New"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "storage_conflict"
    session.refresh(doc)
    assert doc.title == "Old"
```

Append to `backend/tests/test_bulk.py`:

```python
def test_bulk_move_same_titles_get_suffixes(auth_client, session, storage):
    from app.models import DocType, Document, Folder
    from app.services.tree_sync import write_document_file

    target = Folder(name="Target")
    session.add(target)
    session.commit()
    docs = []
    for folder_name in ("A", "B"):
        folder = Folder(name=folder_name)
        session.add(folder)
        session.commit()
        doc = Document(title="X", doc_type=DocType.pdf, folder_id=folder.id)
        session.add(doc)
        session.commit()
        write_document_file(session, storage, doc, ".pdf", folder_name.encode())
        docs.append(doc)

    resp = auth_client.post(
        "/api/documents/bulk/move",
        json={"ids": [str(d.id) for d in docs], "folder_id": target.id},
    )
    assert resp.status_code == 200
    paths = sorted(session.get(Document, d.id).file_path for d in docs)
    assert paths == ["Target/X (2).pdf", "Target/X.pdf"]
    assert {storage.abs_path(p).read_bytes() for p in paths} == {b"A", b"B"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_documents.py tests/test_bulk.py -v -k "rename or title or folder_change or failed_patch or unsafe or suffixes"`
Expected: FAIL (`file_path` unchanged after PATCH)

- [ ] **Step 3: Implement `update_document`**

In `backend/app/api/documents.py`, add imports:

```python
from app.api.storage_errors import storage_errors
from app.services.tree_sync import disk_transaction, lock_documents, relocate_document
```

Replace the body of `update_document` with:

```python
@router.patch("/{document_id}")
def update_document(
    document_id: uuid.UUID,
    body: DocumentPatch,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    get_doc_or_404(session, document_id)
    fields = body.model_dump(exclude_unset=True)
    tag_ids = fields.pop("tag_ids", None)

    if fields.get("document_date", ...) is None:
        fields.pop("document_date", None)  # column is NOT NULL; an empty date input means "unchanged"

    if "folder_id" in fields and fields["folder_id"] is not None:
        if session.get(Folder, fields["folder_id"]) is None:
            raise api_error(404, "not_found", "Folder not found")

    with storage_errors(), disk_transaction(session, storage) as moves:
        [doc] = lock_documents(session, [document_id])
        for key, value in fields.items():
            setattr(doc, key, value)

        if tag_ids is not None:
            for link in session.exec(
                select(DocumentTag).where(DocumentTag.document_id == doc.id)
            ):
                session.delete(link)
            for tag_id in tag_ids:
                if session.get(Tag, tag_id) is None:
                    raise api_error(404, "not_found", f"Tag {tag_id} not found")
                session.add(DocumentTag(document_id=doc.id, tag_id=tag_id))

        if "title" in fields or "folder_id" in fields:
            relocate_document(session, storage, moves, doc)
        doc.updated_at = datetime.now(timezone.utc)
    session.refresh(doc)
    return serialize(session, doc)
```

- [ ] **Step 4: Implement `bulk_move`**

Replace `bulk_move` with:

```python
@router.post("/bulk/move")
def bulk_move(
    body: BulkMove,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    if body.folder_id is not None and session.get(Folder, body.folder_id) is None:
        raise api_error(404, "not_found", "Folder not found")
    now = datetime.now(timezone.utc)
    with storage_errors(), disk_transaction(session, storage) as moves:
        docs = lock_documents(session, body.ids)
        found = {d.id for d in docs}
        for doc in sorted(docs, key=lambda d: (d.created_at, str(d.id))):
            doc.folder_id = body.folder_id
            doc.updated_at = now
            relocate_document(session, storage, moves, doc)
    return {"moved": len(docs), "missing": [str(i) for i in body.ids if i not in found]}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_documents.py tests/test_bulk.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/documents.py backend/tests/test_documents.py backend/tests/test_bulk.py
git commit -m "feat: move document files on rename and folder change"
```

---

### Task 6: Folder directories and ensure-path

**Files:**
- Modify: `backend/app/api/folders.py`
- Test: `backend/tests/test_folders.py`

**Interfaces:**
- Consumes: `folder_rel_dir` (Task 2), `disk_transaction`, `disk_name_taken` (Task 3), `storage_errors`.
- Produces: `POST /api/folders/ensure-path` body `{"parent_id": int | null, "segments": [str]}` → `{"folder_id": int | null}`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_folders.py`:

```python
def _new_folder(auth_client, name, parent_id=None):
    resp = auth_client.post("/api/folders", json={"name": name, "parent_id": parent_id})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_create_folder_makes_directory(auth_client, storage):
    home = _new_folder(auth_client, "Home")
    _new_folder(auth_client, "Bills", home)
    assert storage.abs_path("Home/Bills").is_dir()


def test_sanitized_name_collision_is_409(auth_client, storage):
    _new_folder(auth_client, "a/b")
    resp = auth_client.post("/api/folders", json={"name": "a_b"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"


def test_rename_folder_moves_directory_and_paths(auth_client, session, storage):
    from app.models import DocType, Document
    from app.services.tree_sync import write_document_file

    home = _new_folder(auth_client, "Home")
    bills = _new_folder(auth_client, "Bills", home)
    doc = Document(title="Invoice", doc_type=DocType.pdf, folder_id=bills)
    session.add(doc)
    session.commit()
    write_document_file(session, storage, doc, ".pdf", b"%PDF")

    assert auth_client.patch(f"/api/folders/{home}", json={"name": "House"}).status_code == 200
    session.refresh(doc)
    assert doc.file_path == "House/Bills/Invoice.pdf"
    assert storage.abs_path("House/Bills/Invoice.pdf").exists()
    assert not storage.abs_path("Home").exists()


def test_move_folder_under_another(auth_client, session, storage):
    a = _new_folder(auth_client, "A")
    b = _new_folder(auth_client, "B")
    assert auth_client.patch(f"/api/folders/{b}", json={"parent_id": a}).status_code == 200
    assert storage.abs_path("A/B").is_dir()


def test_move_folder_onto_stray_directory_is_409(auth_client, session, storage):
    from app.models import Folder

    a = _new_folder(auth_client, "A")
    storage.make_dir("B")  # on disk only, not in the database
    resp = auth_client.patch(f"/api/folders/{a}", json={"name": "B"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "storage_conflict"
    session.expire_all()
    assert session.get(Folder, a).name == "A"
    assert storage.abs_path("A").is_dir()


def test_delete_empty_folder_removes_directory(auth_client, storage):
    a = _new_folder(auth_client, "A")
    assert auth_client.delete(f"/api/folders/{a}").status_code == 204
    assert not storage.abs_path("A").exists()


def test_ensure_path_creates_and_reuses(auth_client, session, storage):
    home = _new_folder(auth_client, "Home")
    first = auth_client.post(
        "/api/folders/ensure-path", json={"parent_id": home, "segments": ["Bills", "2025"]}
    ).json()["folder_id"]
    again = auth_client.post(
        "/api/folders/ensure-path", json={"parent_id": home, "segments": ["Bills", "2025"]}
    ).json()["folder_id"]
    assert first == again
    assert storage.abs_path("Home/Bills/2025").is_dir()
    same = auth_client.post(
        "/api/folders/ensure-path", json={"parent_id": home, "segments": []}
    ).json()["folder_id"]
    assert same == home
    root = auth_client.post("/api/folders/ensure-path", json={"parent_id": None, "segments": []})
    assert root.json() == {"folder_id": None}


def test_ensure_path_rejects_empty_segment(auth_client, storage):
    resp = auth_client.post("/api/folders/ensure-path", json={"parent_id": None, "segments": ["A", " "]})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_folders.py -v`
Expected: FAIL (no directories; `ensure-path` 404/405)

- [ ] **Step 3: Implement folder disk handling**

In `backend/app/api/folders.py`, extend imports:

```python
from pydantic import BaseModel, Field
from sqlalchemy import func, update

from app.api.storage_errors import storage_errors
from app.services.storage import Storage, get_storage
from app.services.tree_paths import folder_rel_dir
from app.services.tree_sync import disk_name_taken, disk_transaction
```

Add the request model after `FolderPatch`:

```python
class EnsurePath(BaseModel):
    parent_id: int | None = None
    segments: list[str] = Field(default_factory=list, max_length=64)
```

Replace `create_folder`:

```python
@router.post("", status_code=201)
def create_folder(
    body: FolderCreate,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Folder:
    if body.parent_id is not None:
        get_folder_or_404(session, body.parent_id)
    if disk_name_taken(session, body.parent_id, body.name):
        raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
    folder = Folder(name=body.name, parent_id=body.parent_id)
    with storage_errors(), disk_transaction(session, storage):
        session.add(folder)
        try:
            session.flush()
        except IntegrityError:
            raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
        storage.make_dir(folder_rel_dir(session, folder.id))
    session.refresh(folder)
    return folder
```

Replace `update_folder`:

```python
@router.patch("/{folder_id}")
def update_folder(
    folder_id: int,
    body: FolderPatch,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Folder:
    folder = get_folder_or_404(session, folder_id)
    fields = body.model_dump(exclude_unset=True)
    if "parent_id" in fields and fields["parent_id"] is not None:
        get_folder_or_404(session, fields["parent_id"])
        if is_descendant(session, fields["parent_id"], folder_id):
            raise api_error(409, "folder_cycle", "Cannot move a folder under itself")
    new_parent = fields.get("parent_id", folder.parent_id)
    new_name = fields.get("name", folder.name)
    if disk_name_taken(session, new_parent, new_name, exclude_id=folder_id):
        raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
    old_dir = folder_rel_dir(session, folder_id)
    with storage_errors(), disk_transaction(session, storage) as moves:
        for key, value in fields.items():
            setattr(folder, key, value)
        try:
            session.flush()
        except IntegrityError:
            raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
        new_dir = folder_rel_dir(session, folder_id)
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
    session.refresh(folder)
    return folder
```

Replace `delete_folder`:

```python
@router.delete("/{folder_id}", status_code=204)
def delete_folder(
    folder_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
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
```

Add the endpoint and its helper at the end of the file:

```python
def _child_folder_id(session: Session, storage: Storage, parent_id: int | None, name: str) -> int:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/folders.py backend/tests/test_folders.py
git commit -m "feat: mirror folders on disk and add ensure-path"
```

---

### Task 7: Scan sessions in TMP_PATH; sweep removes leftover part files

**Files:**
- Modify: `backend/app/services/storage.py` (`tmp_scans_dir`)
- Modify: `backend/app/api/scan.py:146-183`
- Modify: `backend/app/worker/pipeline.py` (`_extract_scan`, `sweep_scan_sessions`)
- Modify: `backend/app/api/documents.py:430`
- Test: `backend/tests/test_scan_api.py`, `backend/tests/test_worker.py` or `backend/tests/test_scan_compile.py`

**Interfaces:**
- Consumes: `Storage.tmp_root`, `tmp_abs`, `remove_part_files` (Task 1).
- Produces: `ScanPage.image_path` like `scan_sessions/<id>/<file>.png`, relative to `TMP_PATH`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_scan_api.py`:

```python
def test_scan_pages_live_in_tmp_root(auth_client, fake_scanner, storage, session):
    from app.models import ScanPage

    sid = auth_client.post("/api/scan/sessions", json={}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    page = session.exec(select(ScanPage)).one()
    assert page.image_path.startswith(f"scan_sessions/{sid}/")
    assert storage.tmp_abs(page.image_path).is_file()
    assert storage.tmp_scans_dir == storage.tmp_root / "scan_sessions"
    assert not (storage.root / "tmp").exists()
```

(Add `from sqlmodel import select` at the top of the file if it is missing.)

Append to `backend/tests/test_scan_compile.py`:

```python
def test_sweep_removes_old_part_files(session, storage, monkeypatch):
    import os
    import time

    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    storage.make_dir("A")
    part = storage.abs_path("A/x.pdf.part")
    part.write_bytes(b"")
    past = time.time() - 7200
    os.utime(part, (past, past))
    pipeline.sweep_scan_sessions(session, {})
    assert not part.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scan_api.py::test_scan_pages_live_in_tmp_root tests/test_scan_compile.py::test_sweep_removes_old_part_files -v`
Expected: FAIL (`image_path` starts with `tmp/`; part file still there)

- [ ] **Step 3: Move scan sessions**

In `backend/app/services/storage.py`, change `tmp_scans_dir`:

```python
    @property
    def tmp_scans_dir(self) -> Path:
        d = self.tmp_root / "scan_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d
```

In `backend/app/api/scan.py`:
- `image_path=f"tmp/scan_sessions/{session_id}/{filename}",` → `image_path=f"scan_sessions/{session_id}/{filename}",`
- `FileResponse(storage.abs_path(page.image_path), …)` → `FileResponse(storage.tmp_abs(page.image_path), …)`
- `storage.abs_path(page.image_path).unlink(missing_ok=True)` → `storage.tmp_abs(page.image_path).unlink(missing_ok=True)`

In `backend/app/worker/pipeline.py`, `_extract_scan`: `image_paths = [storage.abs_path(p.image_path) for p in page_rows]` → `image_paths = [storage.tmp_abs(p.image_path) for p in page_rows]`.

In `backend/app/api/documents.py` (`_scan_session_with_pages`): `storage.abs_path(p.image_path).exists()` → `storage.tmp_abs(p.image_path).exists()`.

Check nothing else reads `image_path`:

```bash
grep -rn "image_path" app | grep -v "llm.py\|ocr.py"
```

Expected: only the lines changed above and the model field.

- [ ] **Step 4: Sweep removes leftovers**

In `backend/app/worker/pipeline.py`, add a constant next to `SESSION_MAX_AGE`:

```python
PART_FILE_MAX_AGE = timedelta(hours=1)
```

In `sweep_scan_sessions`, just before the final `enqueue(...)` call:

```python
    for part in storage.remove_part_files(PART_FILE_MAX_AGE.total_seconds()):
        log.info("Removed leftover partial write %s", part)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: keep scan sessions in TMP_PATH and sweep partial writes"
```

---

### Task 8: Storage migration command and startup guard

**Files:**
- Create: `backend/app/services/storage_migration.py`
- Modify: `backend/app/cli.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/worker/__main__.py`
- Test: `backend/tests/test_storage_migration.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `Storage`, `require_new_layout`, `preview_name`, `companion_name` (Task 1), `document_rel_path` (Task 2).
- Produces:
  - `migrate_storage(session, storage, dry_run=False) -> MigrationReport` (`moved: list[tuple[str, str]]`, `missing: list[str]`, `leftovers: list[str]`)
  - `check_storage(session, storage, fix=False) -> CheckReport` (`missing: list[str]`, `unreferenced: list[str]`, `parts: list[str]`, property `ok`)
  - CLI `python -m app.cli migrate-storage [--dry-run] [--check] [--fix]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_storage_migration.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.models import DocType, Document, Folder, ScanPage, ScanSession
from app.services.storage import Storage
from app.services.storage import OldStorageLayout
from app.services.storage_migration import check_storage, migrate_storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "storage")


def old_doc(session, store, title, ext, folder_id=None, data=b"x", **kw):
    doc = Document(title=title, doc_type=kw.pop("doc_type", DocType.pdf), folder_id=folder_id, **kw)
    session.add(doc)
    session.commit()
    rel = f"files/{doc.id}{ext}"
    store.abs_path(rel).parent.mkdir(parents=True, exist_ok=True)
    store.abs_path(rel).write_bytes(data)
    doc.file_path = rel
    session.commit()
    return doc


def test_migrates_documents_previews_companions_and_tmp(session, store):
    home = Folder(name="Home")
    session.add(home)
    session.commit()
    pdf = old_doc(session, store, "Invoice", ".pdf", home.id)
    image = old_doc(session, store, "Photo", ".png", doc_type=DocType.image)
    store.abs_path(f"files/{image.id}.pdf").write_bytes(b"companion")
    office = old_doc(session, store, "Letter", ".docx", doc_type=DocType.text)
    store.abs_path(f"files/{office.id}.preview.pdf").write_bytes(b"preview")
    office.preview_path = f"files/{office.id}.preview.pdf"
    session.commit()
    scan = ScanSession(ocr_languages="ita")
    session.add(scan)
    session.commit()
    store.abs_path(f"tmp/scan_sessions/{scan.id}").mkdir(parents=True)
    store.abs_path(f"tmp/scan_sessions/{scan.id}/page_001.png").write_bytes(b"png")
    session.add(ScanPage(session_id=scan.id, page_number=1, image_path=f"tmp/scan_sessions/{scan.id}/page_001.png"))
    session.commit()
    assert store.has_old_layout()

    report = migrate_storage(session, store)

    session.expire_all()
    assert session.get(Document, pdf.id).file_path == "Home/Invoice.pdf"
    assert session.get(Document, image.id).file_path == "Photo.png"
    assert session.get(Document, office.id).preview_path == f"{office.id}.preview.pdf"
    assert store.derived_abs(f"{image.id}.ocr.pdf").read_bytes() == b"companion"
    assert store.derived_abs(f"{office.id}.preview.pdf").read_bytes() == b"preview"
    page = session.exec(select(ScanPage)).one()
    assert page.image_path == f"scan_sessions/{scan.id}/page_001.png"
    assert store.tmp_abs(page.image_path).read_bytes() == b"png"
    assert not (store.root / "files").exists()
    assert not (store.root / "tmp").exists()
    assert not store.has_old_layout()
    assert report.missing == []


def test_rerun_is_noop(session, store):
    old_doc(session, store, "A", ".pdf")
    migrate_storage(session, store)
    assert migrate_storage(session, store).moved == []


def test_dry_run_changes_nothing(session, store):
    doc = old_doc(session, store, "A", ".pdf")
    report = migrate_storage(session, store, dry_run=True)
    assert report.moved == [(f"files/{doc.id}.pdf", "A.pdf")]
    session.refresh(doc)
    assert doc.file_path == f"files/{doc.id}.pdf"
    assert store.abs_path(f"files/{doc.id}.pdf").exists()


def test_missing_source_is_reported(session, store):
    doc = old_doc(session, store, "A", ".pdf")
    store.abs_path(doc.file_path).unlink()
    report = migrate_storage(session, store)
    assert report.missing == [f"files/{doc.id}.pdf"]


def test_same_titles_get_suffixes(session, store):
    old_doc(session, store, "A", ".pdf", data=b"1")
    old_doc(session, store, "A", ".pdf", data=b"2")
    migrate_storage(session, store)
    assert sorted(p.name for p in store.root.iterdir()) == ["A (2).pdf", "A.pdf"]


def test_check_reports_and_fixes(session, store):
    session.add(Document(title="Gone", doc_type=DocType.pdf, file_path="Gone.pdf"))
    session.commit()
    store.write_file("Stray.pdf", b"1")
    store.abs_path("half.pdf.part").write_bytes(b"")
    report = check_storage(session, store)
    assert report.missing == ["Gone.pdf"]
    assert report.unreferenced == ["Stray.pdf"]
    assert report.parts == ["half.pdf.part"]
    assert not report.ok
    check_storage(session, store, fix=True)
    assert not store.abs_path("half.pdf.part").exists()


def test_api_refuses_to_start_on_old_layout(store):
    from app.main import app
    from app.services import storage as storage_module

    store.abs_path("files").mkdir(parents=True)
    store.abs_path(f"files/{uuid.uuid4()}.pdf").write_bytes(b"%PDF")
    original = storage_module.get_storage
    storage_module.get_storage = lambda: store
    try:
        with pytest.raises(OldStorageLayout, match="migrate-storage"):
            with TestClient(app):
                pass
    finally:
        storage_module.get_storage = original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage_migration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.storage_migration'`

- [ ] **Step 3: Implement `backend/app/services/storage_migration.py`**

```python
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
```

- [ ] **Step 4: Add the CLI command**

In `backend/app/cli.py`, add the function:

```python
def migrate_storage_cmd(dry_run: bool, check: bool, fix: bool) -> None:
    from app.services.storage import get_storage
    from app.services.storage_migration import check_storage, migrate_storage

    storage = get_storage()
    with Session(engine) as session:
        if check or fix:
            report = check_storage(session, storage, fix=fix)
            for rel in report.missing:
                print(f"missing on disk: {rel}")
            for rel in report.unreferenced:
                print(f"not in database: {rel}")
            for rel in report.parts:
                print(f"partial write{' (removed)' if fix else ''}: {rel}")
            print("Storage OK" if report.ok else "Storage has problems (see above)")
            raise SystemExit(0 if report.ok or fix else 1)
        report = migrate_storage(session, storage, dry_run=dry_run)
    for old, new in report.moved:
        print(f"{'would move' if dry_run else 'moved'}: {old} -> {new}")
    for rel in report.missing:
        print(f"missing source (database updated anyway): {rel}")
    for name in report.leftovers:
        print(f"left in files/: {name}")
    if dry_run:
        print("Dry run: nothing changed. Names may get (2), (3) suffixes in the real run.")
    print(f"{len(report.moved)} item(s) {'to move' if dry_run else 'moved'}")
```

In `main()`, register the subcommand:

```python
    p_migrate = sub.add_parser("migrate-storage", help="move files to the folder tree layout")
    p_migrate.add_argument("--dry-run", action="store_true", help="print the moves, change nothing")
    p_migrate.add_argument("--check", action="store_true", help="report database/disk drift")
    p_migrate.add_argument("--fix", action="store_true", help="with --check: remove partial writes")
```

and dispatch:

```python
    elif args.command == "migrate-storage":
        migrate_storage_cmd(args.dry_run, args.check, args.fix)
```

- [ ] **Step 5: Startup guard**

In `backend/app/main.py`, add imports:

```python
from contextlib import asynccontextmanager

from app.services import storage as storage_module
```

Replace `app = FastAPI(title="Origami")` with:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    storage_module.require_new_layout(storage_module.get_storage())
    yield


app = FastAPI(title="Origami", lifespan=lifespan)
```

In `backend/app/worker/__main__.py`, add before `ensure_sweep_scheduled(engine)`:

```python
from app.services.storage import OldStorageLayout, get_storage, require_new_layout

try:
    require_new_layout(get_storage())
except OldStorageLayout as exc:
    raise SystemExit(str(exc))
```

- [ ] **Step 6: CLI test**

Append to `backend/tests/test_cli.py`:

```python
def test_migrate_storage_dry_run_prints(monkeypatch, capsys, tmp_path, engine):
    import sys

    from app import cli
    from app.services.storage import Storage

    store = Storage(tmp_path / "storage")
    monkeypatch.setattr("app.services.storage.get_storage", lambda: store)
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setattr(sys, "argv", ["origami", "migrate-storage", "--dry-run"])
    cli.main()
    assert "0 item(s) to move" in capsys.readouterr().out
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage_migration.py tests/test_cli.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: migrate-storage command and old-layout startup guard"
```

---

### Task 9: Per-document translation language (backend)

**Files:**
- Create: `backend/alembic/versions/b1d7e4f20a93_document_translation_language.py`
- Modify: `backend/app/models/document.py`
- Modify: `backend/app/services/ocr_language_names.py`
- Modify: `backend/app/services/llm.py:24-25`
- Modify: `backend/app/api/ocr.py`
- Modify: `backend/app/api/uploads.py`, `backend/app/api/scan.py`, `backend/app/api/documents.py`
- Modify: `backend/app/worker/pipeline.py` (`_needs_translation`, `_translate_segments`, `translate_document`)
- Test: `backend/tests/test_ocr_languages.py`, `backend/tests/test_pipeline.py`, `backend/tests/test_retranslate.py`, `backend/tests/test_uploads.py`, `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `get_default_translation_language()` (Task 1).
- Produces:
  - `Document.translation_language: str`
  - `TESSERACT_TO_ISO: dict[str, str]`, `iso_language(code) -> str | None`, `iso_language_name(iso) -> str`
  - `translation_languages() -> list[str]`, `default_translation_language() -> str`, `check_translation_language(value: str | None) -> None` in `app/api/ocr.py`
  - `GET /api/ocr/languages` adds `translation_languages: [{code, name}]`, `translation_default: str`
  - serialized documents add `has_text: bool`; `translatable` compares with `translation_language`
  - `translation_language` accepted on upload (form), scan compile, re-process, re-translate (optional JSON body)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ocr_languages.py`:

```python
def test_translation_languages_map_and_dedupe(installed):
    from app.api.ocr import translation_languages

    installed(["eng", "ita", "chi_sim", "chi_tra", "lat", "zzz"])
    assert translation_languages() == ["zh", "en", "it", "la"]  # sorted by name: Chinese, English, Italian, Latin


def test_translation_default_falls_back(installed, monkeypatch):
    from app.api.ocr import default_translation_language
    from app.config import get_settings

    installed(["eng", "deu"])
    monkeypatch.setenv("DEFAULT_TRANSLATION_LANGUAGE", "fr")
    get_settings.cache_clear()
    try:
        assert default_translation_language() == "de"  # first by name: German, English
        monkeypatch.setenv("DEFAULT_TRANSLATION_LANGUAGE", "en")
        get_settings.cache_clear()
        assert default_translation_language() == "en"
    finally:
        get_settings.cache_clear()


def test_languages_endpoint_lists_translation_targets(auth_client, installed):
    installed(["eng", "ita"])
    body = auth_client.get("/api/ocr/languages").json()
    assert body["translation_languages"] == [
        {"code": "en", "name": "English"},
        {"code": "it", "name": "Italian"},
    ]
    assert body["translation_default"] == "it"
```

Append to `backend/tests/test_pipeline.py`:

```python
def test_translation_uses_document_target(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "it"
    doc = make_doc(session, doc_type=DocType.text, title="Nota", translation_language="en")
    pipeline_storage.write_file("Nota.md", "Testo italiano da tradurre.".encode())
    doc.file_path = "Nota.md"
    session.commit()
    run(session, doc)
    job = session.exec(select(Job).where(Job.type == "translate_document")).one()
    pipeline.translate_document(session, job.payload)
    assert {target for _, target in llm_stub["translate"]} == {"en"}


def test_no_translation_when_detected_equals_target(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    doc = make_doc(session, doc_type=DocType.text, title="Notiz", translation_language="de")
    pipeline_storage.write_file("Notiz.md", "Deutscher Text.".encode())
    doc.file_path = "Notiz.md"
    session.commit()
    run(session, doc)
    assert session.exec(select(Job).where(Job.type == "translate_document")).first() is None
```

Append to `backend/tests/test_retranslate.py` (reuse the file's existing ready-document helper; if the helper has another name, adapt the call — it must create a `ready` text document with content chunks and `detected_language="de"`):

```python
def test_retranslate_to_other_target(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(
        session, "Brief", [{"content": "Ein Brief."}],
        detected_language="de", translation_language="it", translation_status="done",
    )
    resp = auth_client.post(f"/api/documents/{doc.id}/retranslate", json={"translation_language": "en"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["translation_language"] == "en"
    assert resp.json()["translation_status"] == "pending"


def test_retranslate_to_own_language_is_409(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Brief", [{"content": "Ein Brief."}], detected_language="de")
    resp = auth_client.post(f"/api/documents/{doc.id}/retranslate", json={"translation_language": "de"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "nothing_to_translate"


def test_retranslate_unknown_target_is_422(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Brief", [{"content": "Ein Brief."}], detected_language="de")
    resp = auth_client.post(f"/api/documents/{doc.id}/retranslate", json={"translation_language": "xx"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_translation_language"
```

Append to `backend/tests/test_uploads.py`:

```python
def test_upload_translation_language(auth_client, storage):
    ok = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"translation_language": "en"},
    )
    assert ok.json()["translation_language"] == "en"
    default = auth_client.post("/api/documents/upload", files={"file": ("b.txt", b"x", "text/plain")})
    assert default.json()["translation_language"] == "it"
    bad = auth_client.post(
        "/api/documents/upload",
        files={"file": ("c.txt", b"x", "text/plain")},
        data={"translation_language": "xx"},
    )
    assert bad.status_code == 422
```

Append to `backend/tests/test_migrations.py`:

```python
def test_translation_language_backfilled(engine):
    cfg = _cfg()
    command.downgrade(cfg, "a6c3e8f15d29")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO documents (id, title, description, doc_type, ocr_languages, ocr_enabled, "
                "summary_enabled, translation_enabled, document_date, status, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'Backfill', '', 'pdf', 'ita', true, true, true, "
                "'2026-01-01', 'ready', now(), now())"
            ))
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            value = conn.execute(
                text("SELECT translation_language FROM documents WHERE title = 'Backfill'")
            ).scalar_one()
        assert value == "it"
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title = 'Backfill'"))
```

The endpoint tests rely on the test machine's Tesseract having `ita`, `eng` and `deu` (README requirement), which gives targets `de`, `en`, `it`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ocr_languages.py tests/test_pipeline.py tests/test_retranslate.py tests/test_uploads.py tests/test_migrations.py -v`
Expected: FAIL (`translation_languages` missing; `Document` has no `translation_language`)

- [ ] **Step 3: Migration and model**

Create `backend/alembic/versions/b1d7e4f20a93_document_translation_language.py`:

```python
"""per-document translation target language

Revision ID: b1d7e4f20a93
Revises: a6c3e8f15d29
Create Date: 2026-10-09 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.config import get_primary_language


revision: str = "b1d7e4f20a93"
down_revision: Union[str, Sequence[str], None] = "a6c3e8f15d29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("translation_language", sa.String(length=8), nullable=True))
    # existing translations were made into PRIMARY_LANGUAGE: keep them valid
    op.execute(
        sa.text("UPDATE documents SET translation_language = :lang").bindparams(lang=get_primary_language())
    )
    op.alter_column("documents", "translation_language", nullable=False)


def downgrade() -> None:
    op.drop_column("documents", "translation_language")
```

In `backend/app/models/document.py`, add the import `from app.config import get_default_translation_language` and the field after `translation_enabled`:

```python
    translation_language: str = Field(default_factory=get_default_translation_language, max_length=8)  # ISO 639-1 target
```

- [ ] **Step 4: Language mapping**

Append to `backend/app/services/ocr_language_names.py`:

```python
TESSERACT_TO_ISO = {
    "afr": "af", "ara": "ar", "bul": "bg", "cat": "ca", "ces": "cs", "chi_sim": "zh", "chi_tra": "zh",
    "dan": "da", "deu": "de", "ell": "el", "eng": "en", "est": "et", "fin": "fi", "fra": "fr",
    "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "ind": "id", "ita": "it", "jpn": "ja",
    "kor": "ko", "lat": "la", "lav": "lv", "lit": "lt", "nld": "nl", "nor": "no", "pol": "pl",
    "por": "pt", "ron": "ro", "rus": "ru", "slk": "sk", "slv": "sl", "spa": "es", "srp": "sr",
    "swe": "sv", "tur": "tr", "ukr": "uk", "vie": "vi",
}

ISO_LANGUAGE_NAMES = {
    iso: ("Chinese" if iso == "zh" else OCR_LANGUAGE_NAMES[code]) for code, iso in TESSERACT_TO_ISO.items()
}


def iso_language(tesseract_code: str) -> str | None:
    """ISO 639-1 code of a Tesseract language; None for scripts and special packs."""
    return TESSERACT_TO_ISO.get(tesseract_code)


def iso_language_name(iso: str) -> str:
    return ISO_LANGUAGE_NAMES.get(iso, iso)
```

In `backend/app/services/llm.py`, import `from app.services.ocr_language_names import iso_language_name` and change `language_name`:

```python
def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code) or iso_language_name(code)
```

- [ ] **Step 5: OCR API**

In `backend/app/api/ocr.py`, add imports:

```python
import logging

from app.config import get_default_translation_language
from app.services.ocr_language_names import iso_language, iso_language_name

log = logging.getLogger("origami.ocr")
```

Add functions:

```python
def translation_languages() -> list[str]:
    """ISO 639-1 targets: installed Tesseract languages that have one, sorted by name."""
    codes = {iso for code in available_languages() if (iso := iso_language(code))}
    return sorted(codes, key=iso_language_name)


def default_translation_language() -> str:
    """DEFAULT_TRANSLATION_LANGUAGE if installed; else the first target (warning)."""
    wanted = get_default_translation_language()
    codes = translation_languages()
    if wanted in codes or not codes:
        return wanted
    log.warning("Translation language %r is not installed; using %r", wanted, codes[0])
    return codes[0]


def check_translation_language(value: str | None) -> None:
    if value is None:
        return
    if value not in translation_languages():
        raise api_error(
            422, "unknown_translation_language", f"Unknown translation language: {value}"
        )
```

Extend `list_ocr_languages`:

```python
@router.get("/languages")
def list_ocr_languages() -> dict:
    return {
        "languages": [{"code": c, "name": ocr_language_name(c)} for c in available_languages()],
        "default": default_ocr_languages(),
        "translation_languages": [
            {"code": c, "name": iso_language_name(c)} for c in translation_languages()
        ],
        "translation_default": default_translation_language(),
    }
```

- [ ] **Step 6: Accept the target on create**

`backend/app/api/uploads.py`:
- Import `check_translation_language, default_translation_language` from `app.api.ocr`.
- `create_pending_document` gains the keyword `translation_language: str | None = None` and passes `translation_language=translation_language or default_translation_language()` to `Document(...)`.
- `upload_document` gains `translation_language: str | None = Form(default=None)`; after the OCR check add `check_translation_language(translation_language)`; pass `translation_language=translation_language` to `create_pending_document`.

`backend/app/api/scan.py`:
- `CompileRequest` gains `translation_language: str | None = None`.
- In `compile_session`, after the OCR check: `check_translation_language(body.translation_language)` (import it from `app.api.ocr`); pass `translation_language=body.translation_language` to `create_pending_document`.

- [ ] **Step 7: Documents API**

In `backend/app/api/documents.py`:
- Import `check_translation_language` from `app.api.ocr`; remove the `get_primary_language` import.
- `serialize`:

```python
    if active_jobs is None:
        active_jobs = active_jobs_for(session, [doc.id])
    if with_content is None:
        with_content = docs_with_content(session, [doc.id])
    has_text = doc.id in with_content
    language_differs = bool(doc.detected_language) and doc.detected_language != doc.translation_language
    return {
        **doc.model_dump(),
        "has_text": has_text,
        "translatable": language_differs and has_text,
        "tags": [t.model_dump() for t in doc_tags(session, doc)],
        "active_job": active_jobs.get(str(doc.id)),
    }
```

- `document_text`: `"translation_language": doc.translation_language,`
- `ReprocessRequest` gains `translation_language: str | None = None`. In `reprocess_document`, next to the OCR check: `check_translation_language(body.translation_language)`; with the other flag assignments: `if body.translation_language: doc.translation_language = body.translation_language`.
- Re-translate:

```python
class RetranslateRequest(BaseModel):
    translation_language: str | None = None


@router.post("/{document_id}/retranslate")
def retranslate_document(
    document_id: uuid.UUID,
    body: RetranslateRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    target = body.translation_language if body and body.translation_language else None
    check_translation_language(target)
    session.refresh(doc, with_for_update=True)  # same lock as reprocess and translate_document
    target = target or doc.translation_language
    if doc.status != DocStatus.ready:
        raise api_error(409, "document_busy", "Document is not ready (still processing or failed)")
    if doc.translation_status == TranslationStatus.pending:
        raise api_error(409, "translation_busy", "A translation is already in progress")
    if not doc.detected_language or doc.detected_language == target:
        raise api_error(409, "nothing_to_translate", "The document is already in the target language")
    if not docs_with_content(session, [doc.id]):
        raise api_error(409, "nothing_to_translate", "The document has no extracted text to translate")
    _cancel_queued_jobs(session, doc)
    for chunk in session.exec(
        select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.translation)
    ):
        session.delete(chunk)
    delete_translation_segments(session, doc.id)
    doc.translation_language = target
    doc.translation_enabled = True
    doc.translation_status = TranslationStatus.pending
    doc.updated_at = datetime.now(timezone.utc)
    session.add(
        Job(type="translate_document", payload={"document_id": str(doc.id)}, run_at=datetime.now(timezone.utc))
    )
    session.commit()
    session.refresh(doc)
    return serialize(session, doc)
```

- [ ] **Step 8: Pipeline reads the document target**

In `backend/app/worker/pipeline.py`:
- `_needs_translation`: `if not doc.detected_language or doc.detected_language == doc.translation_language:`
- `_translate_segments`: `target = doc.translation_language`
- `translate_document`: `if not doc.detected_language or doc.detected_language == doc.translation_language:`
- Remove `get_primary_language` from the `app.config` import if nothing else uses it (`grep -n get_primary_language app/worker/pipeline.py`).

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS. Existing tests that check `translatable` keep passing: test documents default to `translation_language="it"` (the code default of `PRIMARY_LANGUAGE`).

- [ ] **Step 10: Commit**

```bash
git add backend/alembic backend/app backend/tests
git commit -m "feat: per-document translation language with env default"
```

---

### Task 10: Translation target in the UI; scan uses the server OCR default

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/processing.ts`, `frontend/src/lib/processing.test.ts`
- Modify: `frontend/src/lib/upload.ts`, `frontend/src/lib/upload.test.ts`
- Modify: `frontend/src/lib/translation.ts`, `frontend/src/lib/translation.test.ts`
- Modify: `frontend/src/components/ProcessingOptions.tsx`
- Modify: `frontend/src/components/UploadDialog.tsx`
- Modify: `frontend/src/pages/ScanPage.tsx:13,64`
- Modify: `frontend/src/pages/DocumentPage.tsx` (re-translate section)

**Interfaces:**
- Consumes: API fields from Task 9.
- Produces:
  - `ProcessingValues.translationLanguage: string` (`""` = server default)
  - `uploadProcessingFields(p: ProcessingValues): Pick<UploadFields, "ocrLanguages" | "ocrEnabled" | "summaryEnabled" | "translationEnabled" | "translationLanguage">` in `lib/upload.ts`
  - `canRetranslate(doc, target: string): boolean` in `lib/translation.ts`
  - `scanProcessing` is removed.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib/processing.test.ts`:
- Delete the test `"scans default to Italian OCR only"` and remove `scanProcessing` from the import.
- In the first test add `translationLanguage: ""` to the expected object.
- In `"maps to the API payload; empty languages become null"` add `translation_language: null` to the expected object.
- In `"reads a document's stored settings"` add `translation_language: "en"` to `doc` and `translationLanguage: "en"` to the expected object.
- Change the `ocr` constant in `describe("normalizeProcessing")` to:

```ts
    const ocr = {
      languages: [{ code: "eng", name: "English" }, { code: "ita", name: "Italian" }],
      default: "ita+eng",
      translation_languages: [{ code: "en", name: "English" }, { code: "it", name: "Italian" }],
      translation_default: "it",
    };
```

- In `"returns the same object when nothing changes"` use `const value = { ...defaultProcessing(), ocrLanguages: "eng", translationLanguage: "en" };`.
- In `"turns OCR off when no language is installed"` use `const none = { languages: [], default: "", translation_languages: [], translation_default: "it" };` and expect `translationLanguage: "it"` in the first assertion's object; build `off` as `{ ...defaultProcessing(), ocrEnabled: false, translationLanguage: "it" }`.
- Add:

```ts
    it("fills and repairs the translation target", () => {
      expect(normalizeProcessing(defaultProcessing(), ocr).translationLanguage).toBe("it");
      const value = { ...defaultProcessing(), ocrLanguages: "eng", translationLanguage: "de" };
      expect(normalizeProcessing(value, ocr).translationLanguage).toBe("it");
      const kept = { ...defaultProcessing(), ocrLanguages: "eng", translationLanguage: "en" };
      expect(normalizeProcessing(kept, ocr)).toBe(kept);
    });
```

Append to `frontend/src/lib/upload.test.ts`:

```ts
import { defaultProcessing } from "./processing";
import { uploadProcessingFields } from "./upload";

describe("translation language in uploads", () => {
  it("sends the target only when translation is on", () => {
    const on = buildUploadForm(new File(["x"], "a.pdf"), uploadProcessingFields({ ...defaultProcessing(), translationLanguage: "en" }));
    expect(on.get("translation_language")).toBe("en");
    const off = buildUploadForm(
      new File(["x"], "a.pdf"),
      uploadProcessingFields({ ...defaultProcessing(), translationEnabled: false, translationLanguage: "en" }),
    );
    expect(off.get("translation_language")).toBeNull();
    expect(off.get("translation_enabled")).toBe("false");
  });
});
```

(If `buildUploadForm` or `describe` is not yet imported in that file, add them to its existing imports.)

In `frontend/src/lib/translation.test.ts`, replace the `canRetranslate` tests with:

```ts
describe("canRetranslate", () => {
  const ready = { status: "ready", has_text: true, detected_language: "de", translation_status: "done" } as const;

  it("allows a target other than the document language", () => {
    expect(canRetranslate(ready, "it")).toBe(true);
  });

  it("refuses the document's own language, missing text, pending or not ready", () => {
    expect(canRetranslate(ready, "de")).toBe(false);
    expect(canRetranslate({ ...ready, has_text: false }, "it")).toBe(false);
    expect(canRetranslate({ ...ready, translation_status: "pending" }, "it")).toBe(false);
    expect(canRetranslate({ ...ready, status: "processing" }, "it")).toBe(false);
    expect(canRetranslate({ ...ready, detected_language: null }, "it")).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/processing.test.ts src/lib/upload.test.ts src/lib/translation.test.ts`
Expected: FAIL

- [ ] **Step 3: Types**

In `frontend/src/lib/types.ts`, add to `Document` after `translation_enabled`:

```ts
  translation_language: string;
  has_text: boolean;
```

and change `OcrLanguagesResponse`:

```ts
export interface OcrLanguagesResponse {
  languages: OcrLanguage[];
  default: string;
  translation_languages: OcrLanguage[];
  translation_default: string;
}
```

- [ ] **Step 4: `lib/processing.ts`**

Full file:

```ts
import { keepInstalled } from "./ocrLanguages";
import type { Document, OcrLanguagesResponse } from "./types";

export interface ProcessingValues {
  ocrEnabled: boolean;
  ocrLanguages: string; // "" = not chosen yet: use the server default
  summaryEnabled: boolean;
  translationEnabled: boolean;
  translationLanguage: string; // ISO 639-1; "" = not chosen yet: use the server default
}

export function defaultProcessing(): ProcessingValues {
  return { ocrEnabled: true, ocrLanguages: "", summaryEnabled: true, translationEnabled: true, translationLanguage: "" };
}

export function processingFromDocument(doc: Document): ProcessingValues {
  return {
    ocrEnabled: doc.ocr_enabled,
    ocrLanguages: doc.ocr_languages,
    summaryEnabled: doc.summary_enabled,
    translationEnabled: doc.translation_enabled,
    translationLanguage: doc.translation_language,
  };
}

export function processingPayload(v: ProcessingValues) {
  return {
    ocr_enabled: v.ocrEnabled,
    ocr_languages: v.ocrLanguages || null,
    summary_enabled: v.summaryEnabled,
    translation_enabled: v.translationEnabled,
    translation_language: v.translationLanguage || null,
  };
}

/** Fit the values to the installed languages (no OCR language: OCR off); the same object when nothing changes. */
export function normalizeProcessing(v: ProcessingValues, ocr: OcrLanguagesResponse): ProcessingValues {
  const codes = ocr.languages.map((l) => l.code);
  const ocrLanguages = keepInstalled(v.ocrLanguages, codes, ocr.default);
  const ocrEnabled = v.ocrEnabled && codes.length > 0;
  const targets = (ocr.translation_languages ?? []).map((l) => l.code);
  const translationLanguage = targets.includes(v.translationLanguage) ? v.translationLanguage : ocr.translation_default;
  return ocrLanguages === v.ocrLanguages && ocrEnabled === v.ocrEnabled && translationLanguage === v.translationLanguage
    ? v
    : { ...v, ocrEnabled, ocrLanguages, translationLanguage };
}
```

- [ ] **Step 5: `lib/upload.ts`**

Add `translationLanguage?: string;` to `UploadFields`. In `buildUploadForm` add:

```ts
  if (fields.translationLanguage) form.append("translation_language", fields.translationLanguage);
```

Add the import `import type { ProcessingValues } from "./processing";` and:

```ts
/** Upload form fields for the shared processing options; the target is sent only with translation on. */
export function uploadProcessingFields(p: ProcessingValues) {
  return {
    ocrLanguages: p.ocrLanguages || undefined,
    ocrEnabled: p.ocrEnabled,
    summaryEnabled: p.summaryEnabled,
    translationEnabled: p.translationEnabled,
    translationLanguage: p.translationEnabled ? p.translationLanguage || undefined : undefined,
  };
}
```

- [ ] **Step 6: `lib/translation.ts`**

Replace `canRetranslate`:

```ts
export function canRetranslate(
  doc: Pick<Document, "status" | "has_text" | "detected_language" | "translation_status">,
  target: string,
): boolean {
  return (
    doc.status === "ready" &&
    doc.has_text &&
    doc.translation_status !== "pending" &&
    !!doc.detected_language &&
    doc.detected_language !== target
  );
}
```

- [ ] **Step 7: `ProcessingOptions.tsx`**

Add imports:

```tsx
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
```

After the Translation checkbox `<label>…</label>`, before `</fieldset>`:

```tsx
      {value.translationEnabled && (data?.translation_languages.length ?? 0) > 0 && (
        <div className="pl-6">
          <Label htmlFor={`${idPrefix}-tr-lang`}>Translate to</Label>
          <Select
            id={`${idPrefix}-tr-lang`}
            value={value.translationLanguage}
            onChange={(e) => set({ translationLanguage: e.target.value })}
          >
            {data?.translation_languages.map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
          </Select>
        </div>
      )}
```

- [ ] **Step 8: Upload dialog and scan page**

In `UploadDialog.tsx`, import `uploadProcessingFields` from `@/lib/upload` and replace the four processing fields in `buildUploadForm(file, {...})` with `...uploadProcessingFields(processing),`.

In `ScanPage.tsx`: import `defaultProcessing` instead of `scanProcessing`, and use `useState<ProcessingValues>(defaultProcessing)`.

- [ ] **Step 9: Document page re-translate target**

In `DocumentPage.tsx`:
- Import `useOcrLanguages` from `@/hooks/useOcrLanguages` and `Select` from `@/components/ui/select`.
- Inside the component, near the other hooks:

```tsx
  const { data: ocrLanguages } = useOcrLanguages();
  const [retranslateTarget, setRetranslateTarget] = useState<string | null>(null);
```

- Change the `retranslate` mutation body:

```tsx
    mutationFn: () =>
      api.post<Document>(`/api/documents/${id}/retranslate`, {
        translation_language: retranslateTarget ?? doc?.translation_language,
      }),
```

(`doc` is the loaded document variable used elsewhere in the component; keep its existing name.)
- Replace the block `{doc.translatable && ( <> … </> )}` with:

```tsx
            {doc.has_text && (
              <>
                <p className="text-xs text-zinc-500">
                  Translated to {languageLabel(doc.translation_language)}
                </p>
                <Select
                  aria-label="Translation language"
                  value={retranslateTarget ?? doc.translation_language}
                  onChange={(e) => setRetranslateTarget(e.target.value)}
                >
                  {(ocrLanguages?.translation_languages ?? []).map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.name}
                    </option>
                  ))}
                </Select>
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={retranslate.isPending || !canRetranslate(doc, retranslateTarget ?? doc.translation_language)}
                  onClick={() => retranslate.mutate()}
                >
                  {retranslate.isPending ? "Starting…" : "Re-translate"}
                </Button>
                {retranslate.isError && (
                  <p className="text-xs text-red-600">
                    {retranslate.error instanceof ApiError ? retranslate.error.message : "Re-translate failed"}
                  </p>
                )}
              </>
            )}
```

- Extend `LANGUAGE_LABELS` users: `languageLabel` stays as is (it falls back to the upper-case code).

- [ ] **Step 10: Run tests, type check, lint**

Run: `npx vitest run && npx tsc -b && npm run lint`
Expected: PASS, no type errors. Fix any test fixture that builds a `Document` or `OcrLanguagesResponse` literal and now misses the new fields (add `translation_language: "it"`, `has_text: true`, `translation_languages: []`, `translation_default: "it"`).

- [ ] **Step 11: Commit**

```bash
git add frontend/src
git commit -m "feat: translation target select and server OCR default for scans"
```

---

### Task 11: Batch upload planning, queue and drop reading

**Files:**
- Create: `frontend/src/lib/batchUpload.ts`, `frontend/src/lib/batchUpload.test.ts`
- Create: `frontend/src/lib/dropEntries.ts`, `frontend/src/lib/dropEntries.test.ts`
- Modify: `frontend/src/lib/api.ts` (XHR upload with progress)

**Interfaces:**
- Consumes: `buildUploadForm`, `uploadProcessingFields` (Task 10), `ProcessingValues`.
- Produces:
  - `interface PickedFile { file: File; relativePath: string }`
  - `interface BatchItem { key: string; file: File; relativePath: string; title: string; documentDate: string; folderSegments: string[] }`
  - `interface SkippedFile { relativePath: string; reason: string }`
  - `type ItemState = { status: "queued" } | { status: "uploading"; percent: number } | { status: "done" } | { status: "failed"; message: string }`
  - `interface BatchDeps { ensurePath(parentId: number | null, segments: string[]): Promise<number | null>; upload(form: FormData, onProgress: (percent: number) => void): Promise<unknown> }`
  - `interface BatchOptions { folderId: number | null; tagIds: number[]; processing: ProcessingValues }`
  - `UPLOAD_EXTENSIONS`, `planBatch(picked)`, `localIsoDate(ms)`, `runBatch(items, options, deps, onState, concurrency = 3)`, `batchCounts(items, states)`
  - `pickedFromInput(files)`, `pickedFromDataTransfer(dt)` in `lib/dropEntries.ts`
  - `api.upload<T>(path, form, onProgress?)` in `lib/api.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/batchUpload.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { batchCounts, localIsoDate, planBatch, runBatch, type BatchDeps, type ItemState } from "./batchUpload";
import { defaultProcessing } from "./processing";

const file = (name: string, lastModified = new Date(2025, 2, 4, 10).getTime()) =>
  new File(["x"], name, { lastModified });

describe("planBatch", () => {
  it("derives title, date and folder segments", () => {
    const { items, skipped } = planBatch([{ file: file("Invoice ACME.pdf"), relativePath: "Bills/2025/Invoice ACME.pdf" }]);
    expect(skipped).toEqual([]);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      title: "Invoice ACME",
      documentDate: "2025-03-04",
      folderSegments: ["Bills", "2025"],
      relativePath: "Bills/2025/Invoice ACME.pdf",
    });
  });

  it("skips unsupported and hidden files with a reason", () => {
    const { items, skipped } = planBatch([
      { file: file("a.exe"), relativePath: "Docs/a.exe" },
      { file: file(".DS_Store"), relativePath: "Docs/.DS_Store" },
      { file: file("Thumbs.db"), relativePath: "Docs/Thumbs.db" },
      { file: file("b.pdf"), relativePath: "Docs/.git/b.pdf" },
      { file: file("ok.JPG"), relativePath: "ok.JPG" },
    ]);
    expect(items.map((i) => i.relativePath)).toEqual(["ok.JPG"]);
    expect(items[0].folderSegments).toEqual([]);
    expect(skipped).toEqual([
      { relativePath: "Docs/a.exe", reason: "unsupported type .exe" },
      { relativePath: "Docs/.DS_Store", reason: "hidden or system file" },
      { relativePath: "Docs/Thumbs.db", reason: "hidden or system file" },
      { relativePath: "Docs/.git/b.pdf", reason: "hidden or system file" },
    ]);
  });

  it("formats the local date", () => {
    expect(localIsoDate(new Date(2024, 11, 31, 23, 59).getTime())).toBe("2024-12-31");
  });
});

function fakeDeps(overrides: Partial<BatchDeps> = {}) {
  const ensurePath = vi.fn(async (_parent: number | null, segments: string[]) => segments.length * 10);
  const uploads: FormData[] = [];
  const upload = vi.fn(async (form: FormData, onProgress: (p: number) => void) => {
    onProgress(50);
    uploads.push(form);
  });
  return { deps: { ensurePath, upload, ...overrides }, ensurePath, upload, uploads };
}

const options = { folderId: 1, tagIds: [7], processing: { ...defaultProcessing(), translationLanguage: "en" } };

describe("runBatch", () => {
  it("resolves each folder once and uploads every file", async () => {
    const { items } = planBatch([
      { file: file("a.pdf"), relativePath: "A/B/a.pdf" },
      { file: file("b.pdf"), relativePath: "A/B/b.pdf" },
      { file: file("c.pdf"), relativePath: "c.pdf" },
    ]);
    const { deps, ensurePath, uploads } = fakeDeps();
    const states: Record<string, ItemState> = {};
    await runBatch(items, options, deps, (k, s) => (states[k] = s));
    expect(ensurePath).toHaveBeenCalledTimes(1);
    expect(ensurePath).toHaveBeenCalledWith(1, ["A", "B"]);
    expect(uploads.map((f) => f.get("folder_id")).sort()).toEqual(["1", "20", "20"]);
    expect(uploads[0].get("tag_ids")).toBe("7");
    expect(uploads[0].get("translation_language")).toBe("en");
    expect(Object.values(states).every((s) => s.status === "done")).toBe(true);
  });

  it("runs at most three uploads at once", async () => {
    let running = 0;
    let peak = 0;
    const upload = vi.fn(async () => {
      running++;
      peak = Math.max(peak, running);
      await new Promise((r) => setTimeout(r, 5));
      running--;
    });
    const { items } = planBatch(Array.from({ length: 7 }, (_, i) => ({ file: file(`${i}.pdf`), relativePath: `${i}.pdf` })));
    await runBatch(items, options, fakeDeps({ upload }).deps, () => {});
    expect(upload).toHaveBeenCalledTimes(7);
    expect(peak).toBe(3);
  });

  it("fails only the files of a folder that could not be created, and retry resolves again", async () => {
    const { items } = planBatch([
      { file: file("a.pdf"), relativePath: "Bad/a.pdf" },
      { file: file("b.pdf"), relativePath: "b.pdf" },
    ]);
    let calls = 0;
    const ensurePath = vi.fn(async () => {
      calls++;
      if (calls === 1) throw new Error("folder refused");
      return 5;
    });
    const { deps, uploads } = fakeDeps({ ensurePath });
    const states: Record<string, ItemState> = {};
    await runBatch(items, options, deps, (k, s) => (states[k] = s));
    expect(states[items[0].key]).toEqual({ status: "failed", message: "folder refused" });
    expect(states[items[1].key]).toEqual({ status: "done" });
    expect(batchCounts(items, states)).toEqual({ total: 2, done: 1, failed: 1 });

    await runBatch([items[0]], options, deps, (k, s) => (states[k] = s));
    expect(states[items[0].key]).toEqual({ status: "done" });
    expect(uploads.at(-1)?.get("folder_id")).toBe("5");
  });

  it("marks a failed upload and continues", async () => {
    const upload = vi.fn(async (form: FormData) => {
      if ((form.get("file") as File).name === "a.pdf") throw new Error("Unsupported");
    });
    const { items } = planBatch([
      { file: file("a.pdf"), relativePath: "a.pdf" },
      { file: file("b.pdf"), relativePath: "b.pdf" },
    ]);
    const states: Record<string, ItemState> = {};
    await runBatch(items, options, fakeDeps({ upload }).deps, (k, s) => (states[k] = s));
    expect(states[items[0].key]).toEqual({ status: "failed", message: "Unsupported" });
    expect(states[items[1].key]).toEqual({ status: "done" });
  });
});
```

Create `frontend/src/lib/dropEntries.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { pickedFromDataTransfer, pickedFromInput } from "./dropEntries";

function fileEntry(name: string) {
  return { name, isFile: true, isDirectory: false, file: (ok: (f: File) => void) => ok(new File(["x"], name)) };
}

function dirEntry(name: string, children: unknown[]) {
  return {
    name,
    isFile: false,
    isDirectory: true,
    createReader: () => {
      let done = false;
      // real readers return entries in batches and then an empty batch
      return { readEntries: (ok: (e: unknown[]) => void) => { ok(done ? [] : children); done = true; } };
    },
  };
}

describe("dropEntries", () => {
  it("walks dropped folders recursively", async () => {
    const tree = dirEntry("Bills", [fileEntry("a.pdf"), dirEntry("2025", [fileEntry("b.pdf")])]);
    const dt = { items: [{ webkitGetAsEntry: () => tree }, { webkitGetAsEntry: () => fileEntry("c.pdf") }], files: [] };
    const picked = await pickedFromDataTransfer(dt as unknown as DataTransfer);
    expect(picked.map((p) => p.relativePath)).toEqual(["Bills/a.pdf", "Bills/2025/b.pdf", "c.pdf"]);
  });

  it("falls back to plain files without entry support", async () => {
    const f = new File(["x"], "plain.pdf");
    const dt = { items: [{}], files: [f] };
    expect(await pickedFromDataTransfer(dt as unknown as DataTransfer)).toEqual([{ file: f, relativePath: "plain.pdf" }]);
  });

  it("uses webkitRelativePath from a folder input", () => {
    const f = new File(["x"], "a.pdf");
    Object.defineProperty(f, "webkitRelativePath", { value: "Bills/a.pdf" });
    expect(pickedFromInput([f])).toEqual([{ file: f, relativePath: "Bills/a.pdf" }]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/batchUpload.test.ts src/lib/dropEntries.test.ts`
Expected: FAIL (modules not found)

- [ ] **Step 3: `lib/batchUpload.ts`**

```ts
import { buildUploadForm, fileStem, uploadProcessingFields } from "./upload";
import type { ProcessingValues } from "./processing";

/** Must match EXTENSION_MAP in backend/app/api/uploads.py. */
export const UPLOAD_EXTENSIONS = [
  ".pdf", ".txt", ".md", ".doc", ".docx", ".odt", ".rtf",
  ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp",
  ".mp4", ".mkv", ".mov", ".avi", ".webm",
];

const SYSTEM_FILES = new Set(["thumbs.db", "desktop.ini"]);

export interface PickedFile {
  file: File;
  relativePath: string; // "Bills/2025/a.pdf" for folder uploads, "a.pdf" otherwise
}

export interface BatchItem {
  key: string;
  file: File;
  relativePath: string;
  title: string;
  documentDate: string;
  folderSegments: string[];
}

export interface SkippedFile {
  relativePath: string;
  reason: string;
}

export type ItemState =
  | { status: "queued" }
  | { status: "uploading"; percent: number }
  | { status: "done" }
  | { status: "failed"; message: string };

export interface BatchDeps {
  ensurePath: (parentId: number | null, segments: string[]) => Promise<number | null>;
  upload: (form: FormData, onProgress: (percent: number) => void) => Promise<unknown>;
}

export interface BatchOptions {
  folderId: number | null;
  tagIds: number[];
  processing: ProcessingValues;
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

function isHidden(relativePath: string): boolean {
  const parts = relativePath.split("/");
  return parts.some((p) => p.startsWith(".")) || SYSTEM_FILES.has(parts[parts.length - 1].toLowerCase());
}

export function localIsoDate(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function planBatch(picked: PickedFile[]): { items: BatchItem[]; skipped: SkippedFile[] } {
  const items: BatchItem[] = [];
  const skipped: SkippedFile[] = [];
  picked.forEach(({ file, relativePath }, index) => {
    if (isHidden(relativePath)) {
      skipped.push({ relativePath, reason: "hidden or system file" });
      return;
    }
    const ext = extensionOf(file.name);
    if (!UPLOAD_EXTENSIONS.includes(ext)) {
      skipped.push({ relativePath, reason: `unsupported type ${ext || "(none)"}` });
      return;
    }
    items.push({
      key: `${index}:${relativePath}`,
      file,
      relativePath,
      title: fileStem(file.name),
      documentDate: localIsoDate(file.lastModified),
      folderSegments: relativePath.split("/").slice(0, -1),
    });
  });
  return { items, skipped };
}

export async function runBatch(
  items: BatchItem[],
  options: BatchOptions,
  deps: BatchDeps,
  onState: (key: string, state: ItemState) => void,
  concurrency = 3,
): Promise<void> {
  const folders = new Map<string, Promise<number | null>>();
  const folderFor = (segments: string[]): Promise<number | null> => {
    if (segments.length === 0) return Promise.resolve(options.folderId);
    const key = segments.join("/");
    let pending = folders.get(key);
    if (!pending) {
      pending = deps.ensurePath(options.folderId, segments);
      folders.set(key, pending);
      pending.catch(() => folders.delete(key)); // a retry resolves the folder again
    }
    return pending;
  };

  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++];
      try {
        const folderId = await folderFor(item.folderSegments);
        onState(item.key, { status: "uploading", percent: 0 });
        const form = buildUploadForm(item.file, {
          title: item.title,
          documentDate: item.documentDate,
          folderId,
          tagIds: options.tagIds,
          ...uploadProcessingFields(options.processing),
        });
        await deps.upload(form, (percent) => onState(item.key, { status: "uploading", percent }));
        onState(item.key, { status: "done" });
      } catch (err) {
        onState(item.key, { status: "failed", message: err instanceof Error ? err.message : "Upload failed" });
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
}

export function batchCounts(items: BatchItem[], states: Record<string, ItemState>) {
  let done = 0;
  let failed = 0;
  for (const item of items) {
    const status = states[item.key]?.status;
    if (status === "done") done++;
    if (status === "failed") failed++;
  }
  return { total: items.length, done, failed };
}
```

- [ ] **Step 4: `lib/dropEntries.ts`**

```ts
import type { PickedFile } from "./batchUpload";

export function pickedFromInput(files: FileList | File[]): PickedFile[] {
  return Array.from(files).map((file) => ({ file, relativePath: file.webkitRelativePath || file.name }));
}

async function walk(entry: FileSystemEntry, prefix: string, out: PickedFile[]): Promise<void> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    const file = await new Promise<File>((ok, fail) => (entry as FileSystemFileEntry).file(ok, fail));
    out.push({ file, relativePath: path });
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail));
      if (batch.length === 0) break;
      for (const child of batch) await walk(child, path, out);
    }
  }
}

/** Files and folders from a drop; entries are taken synchronously, before the event ends. */
export async function pickedFromDataTransfer(dt: DataTransfer): Promise<PickedFile[]> {
  const entries = Array.from(dt.items)
    .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
    .filter((e): e is FileSystemEntry => e !== null);
  if (entries.length === 0) return pickedFromInput(dt.files);
  const out: PickedFile[] = [];
  for (const entry of entries) await walk(entry, "", out);
  return out;
}
```

- [ ] **Step 5: XHR upload with progress in `lib/api.ts`**

Add before `export const api`:

```ts
/** multipart POST with upload progress (fetch has no upload progress events). */
function uploadForm<T>(path: string, form: FormData, onProgress?: (percent: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    const token = getToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as T);
        return;
      }
      const err = (data as { error?: { code?: string; message?: string; detail?: unknown } })?.error;
      reject(new ApiError(xhr.status, err?.code ?? "unknown_error", err?.message ?? (xhr.statusText || "Upload failed"), err?.detail));
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error"));
    xhr.send(form);
  });
}
```

and add to the `api` object:

```ts
  upload: uploadForm,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `npx vitest run src/lib/batchUpload.test.ts src/lib/dropEntries.test.ts && npx tsc -b`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib
git commit -m "feat: batch upload planning, queue and folder drop reading"
```

---

### Task 12: Batch upload dialog and Browse entry points

**Files:**
- Create: `frontend/src/hooks/useBatchUpload.ts`
- Create: `frontend/src/components/BatchUploadDialog.tsx`
- Modify: `frontend/src/pages/BrowsePage.tsx`
- Test: `frontend/src/components/BatchUploadDialog.test.tsx`

**Interfaces:**
- Consumes: `planBatch`, `runBatch`, `batchCounts`, `BatchItem`, `ItemState`, `PickedFile` (Task 11); `pickedFromInput`, `pickedFromDataTransfer` (Task 11); `api.upload` (Task 11); `ProcessingOptions`, `defaultProcessing` (Task 10).
- Produces: `useBatchUpload(): { states, running, run(items, options), reset() }`; `<BatchUploadDialog picked open onClose initialFolderId />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/BatchUploadDialog.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { BatchUploadDialog } from "./BatchUploadDialog";

vi.mock("@/hooks/useOcrLanguages", () => ({
  useOcrLanguages: () => ({
    data: { languages: [{ code: "ita", name: "Italian" }], default: "ita", translation_languages: [{ code: "it", name: "Italian" }], translation_default: "it" },
  }),
}));
vi.mock("@/hooks/useFolders", () => ({ useFolders: () => ({ data: [] }) }));
vi.mock("@/hooks/useTags", () => ({ useTags: () => ({ data: [] }), useCreateTag: () => ({ mutateAsync: vi.fn() }) }));

function renderDialog(picked: { file: File; relativePath: string }[]) {
  const router = createMemoryRouter([
    { path: "/", element: <BatchUploadDialog picked={picked} open onClose={() => {}} initialFolderId={null} /> },
  ]);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("BatchUploadDialog", () => {
  it("lists accepted and skipped files before upload", () => {
    renderDialog([
      { file: new File(["x"], "a.pdf"), relativePath: "Bills/a.pdf" },
      { file: new File(["x"], "b.exe"), relativePath: "Bills/b.exe" },
    ]);
    expect(screen.getByText("Bills/a.pdf")).toBeInTheDocument();
    expect(screen.getByText(/Bills\/b\.exe.*unsupported type \.exe/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload 1 file" })).toBeEnabled();
  });
});
```

(If `useTags` exports other names in this repo, mock exactly what `TagInput` imports: check `grep -n "from \"@/hooks/useTags\"" src/components/TagInput.tsx`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/BatchUploadDialog.test.tsx`
Expected: FAIL (module not found)

- [ ] **Step 3: `hooks/useBatchUpload.ts`**

```ts
import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { runBatch, type BatchDeps, type BatchItem, type BatchOptions, type ItemState } from "@/lib/batchUpload";

const deps: BatchDeps = {
  ensurePath: (parentId, segments) =>
    api
      .post<{ folder_id: number | null }>("/api/folders/ensure-path", { parent_id: parentId, segments })
      .then((r) => r.folder_id),
  upload: (form, onProgress) => api.upload("/api/documents/upload", form, onProgress),
};

export function useBatchUpload() {
  const qc = useQueryClient();
  const [states, setStates] = useState<Record<string, ItemState>>({});
  const [running, setRunning] = useState(false);

  const run = useCallback(
    async (items: BatchItem[], options: BatchOptions) => {
      setRunning(true);
      setStates((s) => ({ ...s, ...Object.fromEntries(items.map((i) => [i.key, { status: "queued" } as ItemState])) }));
      try {
        await runBatch(items, options, deps, (key, state) => setStates((s) => ({ ...s, [key]: state })));
      } finally {
        setRunning(false);
        qc.invalidateQueries({ queryKey: ["documents"] });
        qc.invalidateQueries({ queryKey: ["folders"] });
      }
    },
    [qc],
  );

  const reset = useCallback(() => setStates({}), []);
  return { states, running, run, reset };
}
```

- [ ] **Step 4: `components/BatchUploadDialog.tsx`**

```tsx
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { FolderPicker } from "@/components/FolderPicker";
import { ProcessingOptions } from "@/components/ProcessingOptions";
import { TagInput } from "@/components/TagInput";
import { useBatchUpload } from "@/hooks/useBatchUpload";
import { useLeaveGuard } from "@/hooks/useLeaveGuard";
import { batchCounts, planBatch, type ItemState, type PickedFile } from "@/lib/batchUpload";
import { defaultProcessing, type ProcessingValues } from "@/lib/processing";

const LEAVE_MESSAGE = "Uploads are still running. Leave anyway? Files not sent yet will not be uploaded.";

function stateLabel(state: ItemState | undefined): string {
  if (!state) return "";
  if (state.status === "uploading") return `${state.percent}%`;
  if (state.status === "failed") return `Failed: ${state.message}`;
  return state.status === "done" ? "Done" : "Queued";
}

function sizeLabel(bytes: number): string {
  return bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function BatchUploadDialog({
  picked,
  open,
  onClose,
  initialFolderId,
}: {
  picked: PickedFile[] | null;
  open: boolean;
  onClose: () => void;
  initialFolderId?: number | null;
}) {
  const plan = useMemo(() => planBatch(picked ?? []), [picked]);
  const [folderId, setFolderId] = useState<number | null>(initialFolderId ?? null);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [processing, setProcessing] = useState<ProcessingValues>(defaultProcessing);
  const batch = useBatchUpload();
  const started = Object.keys(batch.states).length > 0;
  const counts = batchCounts(plan.items, batch.states);

  useLeaveGuard(batch.running, LEAVE_MESSAGE, () => {});

  if (!picked) return null;

  const options = { folderId, tagIds, processing };
  const start = () => void batch.run(plan.items, options);
  const retryFailed = () =>
    void batch.run(plan.items.filter((i) => batch.states[i.key]?.status === "failed"), options);
  const close = () => {
    if (batch.running && !window.confirm(LEAVE_MESSAGE)) return;
    batch.reset();
    onClose();
  };
  const fileWord = (n: number) => `${n} file${n === 1 ? "" : "s"}`;

  return (
    <Dialog open={open} onClose={close} title={`Upload ${fileWord(plan.items.length)}`}>
      <div className="space-y-3">
        <ul className="max-h-48 space-y-1 overflow-y-auto text-sm">
          {plan.items.map((item) => (
            <li key={item.key} className="flex justify-between gap-2">
              <span className="truncate">{item.relativePath}</span>
              <span className="shrink-0 text-zinc-500">
                {started ? stateLabel(batch.states[item.key]) : sizeLabel(item.file.size)}
              </span>
            </li>
          ))}
          {plan.skipped.map((s) => (
            <li key={`skip:${s.relativePath}`} className="text-zinc-400">
              {s.relativePath} — skipped: {s.reason}
            </li>
          ))}
        </ul>
        {started ? (
          <p className="text-sm" role="status">
            {counts.done} of {counts.total} uploaded{counts.failed > 0 ? `, ${counts.failed} failed` : ""}
          </p>
        ) : (
          <>
            <div>
              <Label htmlFor="bu-folder">Folder</Label>
              <FolderPicker id="bu-folder" value={folderId} onChange={setFolderId} />
            </div>
            <div>
              <Label htmlFor="bu-tags">Tags</Label>
              <TagInput id="bu-tags" value={tagIds} onChange={setTagIds} />
            </div>
            <ProcessingOptions idPrefix="bu" value={processing} onChange={setProcessing} />
            <p className="text-xs text-zinc-500">
              Each file keeps its name as title and its last-modified date as document date.
            </p>
          </>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={close}>
            {started && !batch.running ? "Close" : "Cancel"}
          </Button>
          {!started && (
            <Button onClick={start} disabled={plan.items.length === 0}>
              Upload {fileWord(plan.items.length)}
            </Button>
          )}
          {started && !batch.running && counts.failed > 0 && <Button onClick={retryFailed}>Retry failed</Button>}
        </div>
      </div>
    </Dialog>
  );
}
```

- [ ] **Step 5: Browse page entry points**

In `frontend/src/pages/BrowsePage.tsx`:
- Imports:

```tsx
import { BatchUploadDialog } from "@/components/BatchUploadDialog";
import type { PickedFile } from "@/lib/batchUpload";
import { pickedFromDataTransfer, pickedFromInput } from "@/lib/dropEntries";
```

- Next to `fileInput` / `pendingFile` state:

```tsx
  const folderInput = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<PickedFile[] | null>(null);
  const [uploadMenu, setUploadMenu] = useState(false);

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", ""); // not in React's input typings
  }, []);

  const receive = (files: PickedFile[]) => {
    if (files.length === 1 && !files[0].relativePath.includes("/")) setPendingFile(files[0].file);
    else if (files.length > 0) setPicked(files);
  };
```

- Replace `onDrop`:

```tsx
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void pickedFromDataTransfer(e.dataTransfer).then(receive);
  };
```

- Replace the Upload button and the single hidden input with:

```tsx
        <div className="relative">
          <Button onClick={() => setUploadMenu((v) => !v)} aria-haspopup="menu" aria-expanded={uploadMenu}>
            Upload
          </Button>
          {uploadMenu && (
            <div role="menu" className="absolute right-0 z-20 mt-1 w-36 rounded-md border border-zinc-200 bg-white p-1 shadow">
              <button
                role="menuitem"
                className="block w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100"
                onClick={() => {
                  setUploadMenu(false);
                  fileInput.current?.click();
                }}
              >
                Files…
              </button>
              <button
                role="menuitem"
                className="block w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100"
                onClick={() => {
                  setUploadMenu(false);
                  folderInput.current?.click();
                }}
              >
                Folder…
              </button>
            </div>
          )}
        </div>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            receive(pickedFromInput(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        <input
          ref={folderInput}
          type="file"
          hidden
          onChange={(e) => {
            receive(pickedFromInput(e.target.files ?? []));
            e.target.value = "";
          }}
        />
```

- After `<UploadDialog … />`:

```tsx
      {picked && (
        <BatchUploadDialog
          key={picked.map((p) => p.relativePath).join("|")}
          picked={picked}
          open
          onClose={() => setPicked(null)}
          initialFolderId={params.all ? null : params.folderId}
        />
      )}
```

- [ ] **Step 6: Run tests, type check, lint**

Run: `npx vitest run && npx tsc -b && npm run lint`
Expected: PASS

- [ ] **Step 7: Manual check in the app**

Run backend (`uv run uvicorn app.main:app --reload`, worker `uv run python -m app.worker`) and `npm run dev`. In Browse: **Upload → Folder…**, pick a folder with subfolders and one `.exe`; check the dialog lists the skipped file, upload, check folders appear in the explorer and files appear under `STORAGE_PATH` at the same paths. Drag a folder onto Browse: same result.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat: upload several files or a whole folder from Browse"
```

---

### Task 13: Documentation and env example

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: `.env.example`**

After `STORAGE_PATH=./storage` add:

```
# Derived files (office previews, OCR companion PDFs) and temporary scan pages.
# Empty = siblings of STORAGE_PATH named derived/ and tmp/.
DERIVED_PATH=
TMP_PATH=
```

Replace the `PRIMARY_LANGUAGE=…` line with:

```
PRIMARY_LANGUAGE=it  # ISO 639-1 language for AI summaries
# Default translation target for scan and upload (ISO 639-1); empty = PRIMARY_LANGUAGE.
# Must match an installed Tesseract language (ita → it, eng → en, deu → de, …).
DEFAULT_TRANSLATION_LANGUAGE=it
```

- [ ] **Step 2: README**

In **Features**:
- **Upload and scan** bullet: add "Upload several files or a whole folder at once (**Upload → Files…/Folder…**, or drag them onto Browse). Each file keeps its name as title and its last-modified date as document date; folder, tags and processing options apply to all files. A folder upload recreates its subfolders."
- **Processing options** bullet: replace "Scan starts with Italian only, upload with `DEFAULT_OCR_LANGUAGES`" with "Scan and upload start with `DEFAULT_OCR_LANGUAGES`"; add "Translation has a **Translate to** language, default `DEFAULT_TRANSLATION_LANGUAGE`; the choices are the installed OCR languages."
- **Translation** bullet: replace "documents in another language than `PRIMARY_LANGUAGE`" with "documents whose detected language differs from their translation target"; add "Re-translate can switch the target language."
- New bullet **Storage tree:** "`STORAGE_PATH` mirrors the explorer: `Folder/Subfolder/Title.ext`. Renaming or moving a document or folder in the app moves the file on disk. Characters not allowed in file names become `_`; documents with the same title in one folder get `Title (2).ext`. Office previews and OCR companion PDFs live in `DERIVED_PATH`, scan pages in `TMP_PATH`. Changes made directly on disk are not picked up."

In **Installation → Backend**, after `uv run alembic upgrade head`, add:

```markdown
Upgrading from a version with the flat `files/<uuid>` layout: the API and the worker refuse to start until you move the files once:

```bash
uv run python -m app.cli migrate-storage --dry-run   # print the moves
uv run python -m app.cli migrate-storage
uv run python -m app.cli migrate-storage --check     # report database/disk drift; --fix removes partial writes
```
```

- [ ] **Step 3: Commit**

```bash
git add .env.example README.md
git commit -m "docs: storage tree, batch upload and translation language"
```

---

## Final verification

- [ ] `cd backend && uv run pytest -q` — all pass.
- [ ] `cd frontend && npx vitest run && npx tsc -b && npm run lint && npm run build` — all pass.
- [ ] On a copy of real data: `migrate-storage --dry-run`, then run, then `--check` reports OK; start API and worker; open a migrated PDF, an office document (preview) and an OCR'd image.
