# Folder Explorer Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select and bulk move/delete folders together with documents, add a "New folder" button, make the sidebar tree follow navigation, and show subtree document counts on folder tiles.

**Architecture:** A new backend router `/api/bulk` moves or deletes a mixed set of folders and documents in one transaction, reusing helpers extracted from `folders.py` and `documents.py`. The frontend keys its selection as `f:<id>` / `d:<uuid>`, computes subtree counts from the `/api/folders` list, and derives sidebar expansion from the folder open in the URL.

**Tech Stack:** FastAPI + SQLModel + PostgreSQL (pytest, run with `uv run pytest` in `backend/`), React 19 + TanStack Query + Tailwind (vitest, run with `npm test` in `frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-10-folder-explorer-design.md`

## Global Constraints

- Work on branch `feat/folder-explorer`. Commit after every task. Do not push.
- Backend errors use `api_error(status, code, message)`; the JSON shape is `{"error": {"code", "message"}}`.
- Every tree change takes `lock_tree(session)` first, then `session.expire_all()`.
- Never delete a directory recursively on disk. `Storage.remove_dir` stays `rmdir`-only.
- Bulk id lists accept at most `BULK_MAX` (500) entries each; at least one id overall.
- New folder button: `outline` variant, content `📁 New folder` (emoji `aria-hidden`), no plus sign, left of Upload.
- No new npm or Python dependencies. No icon library: use emoji or text glyphs.
- UI copy is English.

## Review Focus

1. A stray directory on disk (not in the DB) at the destination with a selected folder's name: the move must fail with 409 `storage_conflict` and nothing moves. Test in Task 2.
2. A selected folder that already sits in the destination: no-op for that folder, never a `duplicate_folder` error against itself. Test in Task 2.
3. Moving a folder named `files` to root: 409 `reserved_folder_name`. Test in Task 2.
4. Deleting a folder that contains a document with no file yet (`file_path` is `None`, still processing): delete succeeds. Test in Task 3.
5. Deleting the folder currently open (or one of its ancestors) from the sidebar: the explorer navigates to the deleted folder's parent. Test in Task 9.

---

## File Structure

Backend:
- Modify `backend/app/api/folders.py`: extract `reparent_folder()` from `update_folder`.
- Modify `backend/app/api/documents.py`: rename `_delete_documents` to `delete_documents`, extract `move_documents()` from `bulk_move`.
- Create `backend/app/api/bulk.py`: `/api/bulk/move` and `/api/bulk/delete`.
- Modify `backend/app/main.py`: register the router.
- Create `backend/tests/test_bulk_items.py`.

Frontend:
- Modify `frontend/src/lib/selection.ts`: `folderKey`, `docKey`, `splitKeys`.
- Modify `frontend/src/lib/folderTree.ts`: `descendantIds`, `subtreeTotals`, `subtreeDocumentCount`, `ancestorIds`.
- Create `frontend/src/lib/counts.ts`: `plural`, `itemsLabel`.
- Modify `frontend/src/lib/types.ts`: `BulkResult` → `BulkItemsResult`.
- Modify `frontend/src/hooks/useDocuments.ts`: `useBulkMoveItems`, `useBulkDeleteItems` replace `useBulkMove`, `useBulkDelete`.
- Modify `frontend/src/hooks/useFolders.ts`: remove `useDeleteFolder`.
- Create `frontend/src/components/NewFolderDialog.tsx` (+ test).
- Modify `frontend/src/components/FolderPicker.tsx`: `disabledIds` prop.
- Modify `frontend/src/components/FolderTiles.tsx`: checkbox, subtree counts.
- Modify `frontend/src/components/BulkActionBar.tsx`: mixed counts.
- Modify `frontend/src/components/FolderTree.tsx`: navigation-driven expansion, dialog, recursive delete (+ new test).
- Modify `frontend/src/pages/BrowsePage.tsx`: wiring and New folder button.

---

### Task 1: Extract backend move and delete helpers

Pure refactor. Existing tests are the safety net.

**Files:**
- Modify: `backend/app/api/folders.py` (function `update_folder`)
- Modify: `backend/app/api/documents.py` (functions `_delete_documents`, `bulk_move`, `bulk_delete`, `delete_document`)

**Interfaces:**
- Produces:
  - `app.api.folders.reparent_folder(session: Session, moves: MoveLog, folder: Folder, fields: dict) -> None`: applies `fields` (keys `name` and/or `parent_id`) to `folder`, flushes, moves its directory, rewrites `file_path` of documents under it. Raises `api_error(409, "duplicate_folder", ...)` on `IntegrityError`.
  - `app.api.documents.move_documents(session: Session, storage: Storage, moves: MoveLog, docs: list[Document], folder_id: int | None) -> None`
  - `app.api.documents.delete_documents(session: Session, docs: list[Document]) -> list[tuple]` (renamed from `_delete_documents`, same behaviour).

- [ ] **Step 1: Run the existing tests as a baseline**

Run: `cd /opt/origami/backend && uv run pytest tests/test_folders.py tests/test_bulk.py tests/test_documents.py -q`
Expected: all PASS.

- [ ] **Step 2: Extract `reparent_folder` in `folders.py`**

Add the import `from app.services.tree_sync import MoveLog` to the existing `tree_sync` import line (keep the other names). Add this function above `update_folder`:

```python
def reparent_folder(session: Session, moves: MoveLog, folder: Folder, fields: dict) -> None:
    """Rename and/or move a folder: its directory moves and document paths under it follow."""
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
```

In `update_folder`, replace everything from `old_dir = folder_rel_dir(session, folder_id)` down to the end of the `if new_dir != old_dir:` block with:

```python
        reparent_folder(session, moves, folder, fields)
```

- [ ] **Step 3: Extract `move_documents` and rename `delete_documents` in `documents.py`**

Rename `_delete_documents` to `delete_documents` (definition and its two callers, in `bulk_delete` and `delete_document`). Add `MoveLog` to the `tree_sync` import line. Add above `bulk_move`:

```python
def move_documents(
    session: Session, storage: Storage, moves: MoveLog, docs: list[Document], folder_id: int | None
) -> None:
    """Put the documents in `folder_id` and move their files (call with the tree and rows locked)."""
    now = datetime.now(timezone.utc)
    for doc in sorted(docs, key=lambda d: (d.created_at, str(d.id))):
        doc.folder_id = folder_id
        doc.updated_at = now
        relocate_document(session, storage, moves, doc)
```

Replace the body of `bulk_move` with:

```python
    if body.folder_id is not None and session.get(Folder, body.folder_id) is None:
        raise api_error(404, "not_found", "Folder not found")
    with storage_errors(), disk_transaction(session, storage) as moves:
        lock_tree(session)  # before the row locks: same order as write_document_file
        docs = lock_documents(session, body.ids)
        found = {d.id for d in docs}
        move_documents(session, storage, moves, docs, body.folder_id)
    return {"moved": len(docs), "missing": [str(i) for i in body.ids if i not in found]}
```

- [ ] **Step 4: Run the tests again**

Run: `cd /opt/origami/backend && uv run pytest tests/test_folders.py tests/test_bulk.py tests/test_documents.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami && git add backend/app/api/folders.py backend/app/api/documents.py
git commit -m "refactor: extract folder reparent and document move/delete helpers"
```

---

### Task 2: `POST /api/bulk/move`

**Files:**
- Create: `backend/app/api/bulk.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_bulk_items.py`

**Interfaces:**
- Consumes: `reparent_folder`, `move_documents`, `check_reserved_name`, `get_folder_or_404`, `is_descendant` (folders.py), `BULK_MAX` (documents.py).
- Produces:
  - `POST /api/bulk/move` body `{"folder_ids": int[], "document_ids": uuid[], "folder_id": int | null}` → `{"moved_folders": int, "moved_documents": int, "missing_folders": int[], "missing_documents": str[]}`.
  - In `bulk.py`: `class BulkItems(BaseModel)` with `folder_ids`, `document_ids`; `subtree_ids(session, root_ids: list[int]) -> list[int]` (breadth-first, parents before children); `existing_folders(session, ids: list[int]) -> tuple[list[int], list[int]]` (found, missing; de-duplicated, input order).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_bulk_items.py`:

```python
import uuid

from sqlmodel import select

from app.models import DocStatus, DocType, Document, Folder, Job, JobStatus
from app.services.tree_sync import write_document_file


def new_folder(client, name, parent=None):
    resp = client.post("/api/folders", json={"name": name, "parent_id": parent})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def new_doc(session, storage, title, folder_id=None, with_file=True):
    doc = Document(title=title, doc_type=DocType.pdf, status=DocStatus.ready, folder_id=folder_id)
    session.add(doc)
    session.commit()
    if with_file:
        write_document_file(session, storage, doc, ".pdf", b"%PDF")
    session.refresh(doc)
    return doc


def move(client, folder_id, folders=(), docs=()):
    return client.post(
        "/api/bulk/move",
        json={"folder_ids": list(folders), "document_ids": [str(d) for d in docs], "folder_id": folder_id},
    )


def test_move_folders_and_documents(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    target = new_folder(auth_client, "Target")
    inside = new_doc(session, storage, "Inside", a)
    loose = new_doc(session, storage, "Loose")

    resp = move(auth_client, target, folders=[a], docs=[loose.id])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"moved_folders": 1, "moved_documents": 1, "missing_folders": [], "missing_documents": []}
    session.expire_all()
    assert session.get(Folder, a).parent_id == target
    assert session.get(Document, inside.id).file_path == "Target/A/Inside.pdf"
    assert session.get(Document, loose.id).file_path == "Target/Loose.pdf"
    assert storage.abs_path("Target/A/Inside.pdf").exists()
    assert storage.abs_path("Target/Loose.pdf").exists()
    assert not storage.abs_path("A").exists()


def test_move_into_selected_folder_or_descendant_is_cycle(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    b = new_folder(auth_client, "B", a)
    for target in (a, b):
        resp = move(auth_client, target, folders=[a])
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "folder_cycle"
    session.expire_all()
    assert session.get(Folder, a).parent_id is None


def test_name_conflict_rejects_everything(auth_client, session, storage):
    target = new_folder(auth_client, "Target")
    new_folder(auth_client, "X", target)
    elsewhere = new_folder(auth_client, "Elsewhere")
    x = new_folder(auth_client, "X", elsewhere)
    loose = new_doc(session, storage, "Loose")

    resp = move(auth_client, target, folders=[x], docs=[loose.id])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"
    assert "X" in resp.json()["error"]["message"]
    session.expire_all()
    assert session.get(Folder, x).parent_id == elsewhere
    assert session.get(Document, loose.id).folder_id is None
    assert storage.abs_path("Elsewhere/X").is_dir()
    assert storage.abs_path("Loose.pdf").exists()


def test_two_selected_folders_with_same_name_conflict(auth_client, session, storage):
    p1 = new_folder(auth_client, "P1")
    p2 = new_folder(auth_client, "P2")
    x1 = new_folder(auth_client, "X", p1)
    x2 = new_folder(auth_client, "X", p2)
    target = new_folder(auth_client, "Target")
    resp = move(auth_client, target, folders=[x1, x2])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"
    session.expire_all()
    assert session.get(Folder, x1).parent_id == p1


def test_nested_selection_moves_once(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    b = new_folder(auth_client, "B", a)
    doc = new_doc(session, storage, "Deep", b)
    target = new_folder(auth_client, "Target")

    resp = move(auth_client, target, folders=[b, a], docs=[doc.id])
    assert resp.status_code == 200, resp.text
    session.expire_all()
    assert session.get(Folder, a).parent_id == target
    assert session.get(Folder, b).parent_id == a
    assert session.get(Document, doc.id).folder_id == b
    assert session.get(Document, doc.id).file_path == "Target/A/B/Deep.pdf"


def test_folder_already_in_destination_is_noop(auth_client, session, storage):
    target = new_folder(auth_client, "Target")
    x = new_folder(auth_client, "X", target)
    resp = move(auth_client, target, folders=[x])
    assert resp.status_code == 200, resp.text
    assert storage.abs_path("Target/X").is_dir()


def test_stray_directory_at_destination_rolls_back(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    target = new_folder(auth_client, "Target")
    storage.make_dir("Target/A")  # on disk only
    loose = new_doc(session, storage, "Loose")
    resp = move(auth_client, target, folders=[a], docs=[loose.id])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "storage_conflict"
    session.expire_all()
    assert session.get(Folder, a).parent_id is None
    assert storage.abs_path("A").is_dir()
    assert storage.abs_path("Loose.pdf").exists()


def test_move_folder_named_files_to_root_is_reserved(auth_client, session, storage):
    parent = new_folder(auth_client, "Parent")
    files = new_folder(auth_client, "files", parent)
    resp = move(auth_client, None, folders=[files])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "reserved_folder_name"


def test_move_reports_missing_and_checks_destination(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    ghost = uuid.uuid4()
    resp = move(auth_client, None, folders=[a, 999], docs=[ghost])
    assert resp.status_code == 200, resp.text
    assert resp.json()["missing_folders"] == [999]
    assert resp.json()["missing_documents"] == [str(ghost)]
    assert move(auth_client, 999, folders=[a]).status_code == 404


def test_bulk_items_validation_and_auth(client, auth_client):
    assert auth_client.post("/api/bulk/move", json={"folder_id": None}).status_code == 422
    assert auth_client.post("/api/bulk/delete", json={"folder_ids": [], "document_ids": []}).status_code == 422
    too_many = list(range(1, 502))
    assert auth_client.post("/api/bulk/delete", json={"folder_ids": too_many}).status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_bulk_items.py -q`
Expected: FAIL (404 on `/api/bulk/move`).

- [ ] **Step 3: Implement `bulk.py` (move only)**

Create `backend/app/api/bulk.py`:

```python
"""Bulk actions on a mixed selection of folders and documents, each in one transaction."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.api.documents import BULK_MAX, move_documents
from app.api.folders import check_reserved_name, get_folder_or_404, is_descendant, reparent_folder
from app.api.storage_errors import storage_errors
from app.db import get_session
from app.models import Folder
from app.services.storage import Storage, get_storage
from app.services.tree_paths import safe_name
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
```

In `backend/app/main.py`, add `bulk` to the `from app.api import ...` line (alphabetical: `agent, auth, bulk, chat, ...`) and add `app.include_router(bulk.router)` after `app.include_router(auth.router)`.

- [ ] **Step 4: Run tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_bulk_items.py -q`
Expected: all move tests PASS. `test_bulk_items_validation_and_auth` fails only on its `/api/bulk/delete` lines (404 instead of 422); Task 3 fixes it.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami && git add backend/app/api/bulk.py backend/app/main.py backend/tests/test_bulk_items.py
git commit -m "feat: bulk move of folders and documents in one transaction"
```

---

### Task 3: `POST /api/bulk/delete` (recursive)

**Files:**
- Modify: `backend/app/api/bulk.py`
- Test: `backend/tests/test_bulk_items.py`

**Interfaces:**
- Consumes: `delete_documents` (documents.py), `subtree_ids`, `existing_folders`, `BulkItems` (bulk.py), `folder_rel_dir`.
- Produces: `POST /api/bulk/delete` body `{"folder_ids": int[], "document_ids": uuid[]}` → `{"deleted_folders": int, "deleted_documents": int, "missing_folders": int[], "missing_documents": str[]}`. `deleted_folders` and `deleted_documents` include everything inside the selected folders.

- [ ] **Step 1: Append failing tests to `backend/tests/test_bulk_items.py`**

```python
def delete(client, folders=(), docs=()):
    return client.post(
        "/api/bulk/delete", json={"folder_ids": list(folders), "document_ids": [str(d) for d in docs]}
    )


def test_delete_is_recursive(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    b = new_folder(auth_client, "B", a)
    keep = new_folder(auth_client, "Keep")
    in_a = new_doc(session, storage, "InA", a)
    in_b = new_doc(session, storage, "InB", b)
    pending = new_doc(session, storage, "Pending", b, with_file=False)  # still processing: no file yet
    loose = new_doc(session, storage, "Loose")
    kept = new_doc(session, storage, "Kept", keep)
    session.add(Job(type="process_document", payload={"document_id": str(in_b.id)}))
    session.commit()

    resp = delete(auth_client, folders=[a], docs=[loose.id, in_a.id])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted_folders": 2, "deleted_documents": 4, "missing_folders": [], "missing_documents": []}
    session.expire_all()
    assert session.exec(select(Folder.id)).all() == [keep]
    assert [d.id for d in session.exec(select(Document))] == [kept.id]
    assert session.exec(select(Job)).one().status == JobStatus.cancelled
    assert not storage.abs_path("A").exists()
    assert not storage.abs_path("Loose.pdf").exists()
    assert storage.abs_path("Keep/Kept.pdf").exists()
    assert pending.id not in {d.id for d in session.exec(select(Document))}


def test_delete_keeps_directory_with_untracked_file(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    storage.write_file("A/notes.txt", b"mine")  # not a document
    resp = delete(auth_client, folders=[a])
    assert resp.status_code == 200, resp.text
    session.expire_all()
    assert session.get(Folder, a) is None
    assert storage.abs_path("A/notes.txt").read_bytes() == b"mine"


def test_delete_reports_missing(auth_client, session, storage):
    ghost = uuid.uuid4()
    resp = delete(auth_client, folders=[999], docs=[ghost])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "deleted_folders": 0, "deleted_documents": 0, "missing_folders": [999], "missing_documents": [str(ghost)],
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_bulk_items.py -q -k "delete or validation"`
Expected: FAIL (404 on `/api/bulk/delete`).

- [ ] **Step 3: Implement delete in `bulk.py`**

Change imports: `from app.api.documents import BULK_MAX, delete_documents, move_documents`, `from app.models import Document, Folder`, `from app.services.tree_paths import folder_rel_dir, safe_name`. Append:

```python
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
```

- [ ] **Step 4: Run all backend tests**

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami && git add backend/app/api/bulk.py backend/tests/test_bulk_items.py
git commit -m "feat: recursive bulk delete of folders and documents"
```

---

### Task 4: Frontend selection keys, tree helpers and count labels

**Files:**
- Modify: `frontend/src/lib/selection.ts`, `frontend/src/lib/folderTree.ts`
- Create: `frontend/src/lib/counts.ts`
- Test: `frontend/src/lib/selection.test.ts`, `frontend/src/lib/folderTree.test.ts`, create `frontend/src/lib/counts.test.ts`

**Interfaces:**
- Produces:
  - `folderKey(id: number): string`, `docKey(id: string): string`, `splitKeys(keys: Iterable<string>): { folderIds: number[]; documentIds: string[] }`
  - `descendantIds(folders: Folder[], ids: Iterable<number>): Set<number>` (includes `ids`)
  - `subtreeTotals(folders: Folder[], ids: Iterable<number>): { folders: number; documents: number }`
  - `subtreeDocumentCount(folders: Folder[], id: number): number`
  - `ancestorIds(folders: Folder[], id: number | null): Set<number>` (includes `id`)
  - `plural(n: number, word: string): string`, `itemsLabel(folders: number, documents: number, joiner?: string): string`

- [ ] **Step 1: Write failing tests**

Append to `frontend/src/lib/selection.test.ts` (add `docKey, folderKey, splitKeys` to its existing import from `./selection`):

```ts
it("keys folders and documents apart and splits them back", () => {
  expect(folderKey(7)).toBe("f:7");
  expect(docKey("ab-1")).toBe("d:ab-1");
  expect(splitKeys(["f:7", "d:ab-1", "f:12"])).toEqual({ folderIds: [7, 12], documentIds: ["ab-1"] });
});
```

Append to `frontend/src/lib/folderTree.test.ts` (add `ancestorIds, descendantIds, subtreeDocumentCount, subtreeTotals` to its import from `./folderTree`):

```ts
const TREE = [
  { id: 1, name: "Bollette", parent_id: null, created_at: "", document_count: 2 },
  { id: 2, name: "2026", parent_id: 1, created_at: "", document_count: 3 },
  { id: 3, name: "Gennaio", parent_id: 2, created_at: "", document_count: 4 },
  { id: 4, name: "Auto", parent_id: null, created_at: "", document_count: 1 },
];

it("collects descendants and sums subtree documents", () => {
  expect(descendantIds(TREE, [2])).toEqual(new Set([2, 3]));
  expect(subtreeDocumentCount(TREE, 1)).toBe(9);
  expect(subtreeDocumentCount(TREE, 3)).toBe(4);
  expect(subtreeTotals(TREE, [1, 2, 4])).toEqual({ folders: 4, documents: 10 });
  expect(subtreeTotals(TREE, [])).toEqual({ folders: 0, documents: 0 });
});

it("lists a folder and its ancestors", () => {
  expect(ancestorIds(TREE, 3)).toEqual(new Set([1, 2, 3]));
  expect(ancestorIds(TREE, null)).toEqual(new Set());
});
```

Create `frontend/src/lib/counts.test.ts`:

```ts
import { expect, it } from "vitest";
import { itemsLabel, plural } from "./counts";

it("pluralizes", () => {
  expect(plural(1, "folder")).toBe("1 folder");
  expect(plural(3, "document")).toBe("3 documents");
});

it("labels mixed selections and omits an empty kind", () => {
  expect(itemsLabel(2, 1)).toBe("2 folders, 1 document");
  expect(itemsLabel(0, 5)).toBe("5 documents");
  expect(itemsLabel(1, 0)).toBe("1 folder");
  expect(itemsLabel(3, 7, " and ")).toBe("3 folders and 7 documents");
  expect(itemsLabel(0, 0)).toBe("0 documents");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/selection.test.ts src/lib/folderTree.test.ts src/lib/counts.test.ts`
Expected: FAIL (missing exports / module).

- [ ] **Step 3: Implement**

Append to `frontend/src/lib/selection.ts`:

```ts
/** Selection keys: folders and documents share one selection. */
export const folderKey = (id: number): string => `f:${id}`;
export const docKey = (id: string): string => `d:${id}`;

export function splitKeys(keys: Iterable<string>): { folderIds: number[]; documentIds: string[] } {
  const folderIds: number[] = [];
  const documentIds: string[] = [];
  for (const key of keys) {
    if (key.startsWith("f:")) folderIds.push(Number(key.slice(2)));
    else if (key.startsWith("d:")) documentIds.push(key.slice(2));
  }
  return { folderIds, documentIds };
}
```

Append to `frontend/src/lib/folderTree.ts`:

```ts
/** `ids` plus every folder below them. */
export function descendantIds(folders: Folder[], ids: Iterable<number>): Set<number> {
  const result = new Set(ids);
  let grew = result.size > 0;
  while (grew) {
    grew = false;
    for (const f of folders) {
      if (f.parent_id !== null && result.has(f.parent_id) && !result.has(f.id)) {
        result.add(f.id);
        grew = true;
      }
    }
  }
  return result;
}

/** Folders and documents in the subtrees of `ids` (the folders themselves included). */
export function subtreeTotals(folders: Folder[], ids: Iterable<number>): { folders: number; documents: number } {
  const all = descendantIds(folders, ids);
  const documents = folders.reduce((n, f) => (all.has(f.id) ? n + f.document_count : n), 0);
  return { folders: all.size, documents };
}

export function subtreeDocumentCount(folders: Folder[], id: number): number {
  return subtreeTotals(folders, [id]).documents;
}

/** The folder and all its ancestors (empty for the root). */
export function ancestorIds(folders: Folder[], id: number | null): Set<number> {
  return new Set(folderPath(folders, id).map((f) => f.id));
}
```

Create `frontend/src/lib/counts.ts`:

```ts
export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "2 folders, 1 document"; a kind with zero items is left out (documents shown when both are zero). */
export function itemsLabel(folders: number, documents: number, joiner = ", "): string {
  const parts: string[] = [];
  if (folders > 0) parts.push(plural(folders, "folder"));
  if (documents > 0 || folders === 0) parts.push(plural(documents, "document"));
  return parts.join(joiner);
}
```

- [ ] **Step 4: Run tests**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/selection.test.ts src/lib/folderTree.test.ts src/lib/counts.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami && git add frontend/src/lib/selection.ts frontend/src/lib/selection.test.ts frontend/src/lib/folderTree.ts frontend/src/lib/folderTree.test.ts frontend/src/lib/counts.ts frontend/src/lib/counts.test.ts
git commit -m "feat: selection keys, subtree totals and count labels"
```

---

### Task 5: New folder dialog and button

**Files:**
- Create: `frontend/src/components/NewFolderDialog.tsx`, `frontend/src/components/NewFolderDialog.test.tsx`
- Modify: `frontend/src/pages/BrowsePage.tsx` (toolbar, before the `data-upload-menu` div)

**Interfaces:**
- Consumes: `useCreateFolder()` from `hooks/useFolders.ts`.
- Produces: `NewFolderDialog({ open: boolean; parentId: number | null; onClose: () => void })`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/NewFolderDialog.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NewFolderDialog } from "./NewFolderDialog";

const fetchMock = vi.fn();
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function renderDialog(parentId: number | null = 5) {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NewFolderDialog open parentId={parentId} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

it("creates the folder in the given parent on Enter", async () => {
  fetchMock.mockImplementation(async () => json({ id: 9, name: "Bills", parent_id: 5 }, 201));
  const onClose = renderDialog(5);
  await userEvent.type(screen.getByLabelText("Folder name"), "  Bills {Enter}");
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/folders",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Bills", parent_id: 5 }) }),
  );
});

it("shows the server error and stays open", async () => {
  fetchMock.mockImplementation(async () =>
    json({ error: { code: "duplicate_folder", message: "Sibling folder with same name exists" } }, 409),
  );
  const onClose = renderDialog();
  await userEvent.type(screen.getByLabelText("Folder name"), "Bills");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText("Sibling folder with same name exists")).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

it("rejects an empty name without calling the server", async () => {
  renderDialog();
  await userEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(screen.getByText("Enter a folder name")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/origami/frontend && npx vitest run src/components/NewFolderDialog.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the dialog**

Create `frontend/src/components/NewFolderDialog.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useCreateFolder } from "@/hooks/useFolders";
import { ApiError } from "@/lib/api";

export function NewFolderDialog({
  open,
  parentId,
  onClose,
}: {
  open: boolean;
  parentId: number | null;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateFolder();

  const close = () => {
    setName("");
    setError(null);
    onClose();
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Enter a folder name");
      return;
    }
    create.mutate(
      { name: trimmed, parent_id: parentId },
      {
        onSuccess: close,
        onError: (err) => setError(err instanceof ApiError ? err.message : "Could not create the folder"),
      },
    );
  };

  return (
    <Dialog open={open} onClose={close} title="New folder">
      <form onSubmit={submit} className="space-y-3">
        <Input
          autoFocus
          aria-label="Folder name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError(null);
          }}
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending}>
            Create
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
```

- [ ] **Step 4: Add the button to `BrowsePage.tsx`**

Import: `import { NewFolderDialog } from "@/components/NewFolderDialog";`. Add state next to `uploadMenu`:

```tsx
  const [newFolder, setNewFolder] = useState(false);
```

Insert directly before `<div className="relative" data-upload-menu>`:

```tsx
        <Button variant="outline" onClick={() => setNewFolder(true)}>
          <span aria-hidden="true" className="mr-1.5">
            📁
          </span>
          New folder
        </Button>
```

Insert directly before `<UploadDialog`:

```tsx
      <NewFolderDialog
        open={newFolder}
        parentId={params.all ? null : params.folderId}
        onClose={() => setNewFolder(false)}
      />
```

- [ ] **Step 5: Run tests and type check**

Run: `cd /opt/origami/frontend && npx vitest run src/components/NewFolderDialog.test.tsx && npx tsc -b`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami && git add frontend/src/components/NewFolderDialog.tsx frontend/src/components/NewFolderDialog.test.tsx frontend/src/pages/BrowsePage.tsx
git commit -m "feat: New folder button and dialog in the explorer"
```

---

### Task 6: Bulk item hooks and types

**Files:**
- Modify: `frontend/src/lib/types.ts` (`BulkResult`), `frontend/src/hooks/useDocuments.ts` (`useBulkMove`, `useBulkDelete`)

**Interfaces:**
- Produces:
  - `interface BulkItems { folder_ids: number[]; document_ids: string[] }` (exported from `hooks/useDocuments.ts`)
  - `interface BulkItemsResult { moved_folders?: number; moved_documents?: number; deleted_folders?: number; deleted_documents?: number; missing_folders: number[]; missing_documents: string[] }` (in `lib/types.ts`)
  - `useBulkMoveItems()`: mutation taking `BulkItems & { folder_id: number | null }`
  - `useBulkDeleteItems()`: mutation taking `BulkItems`
  - Both invalidate `["documents"]` and `["folders"]`.
- Removes: `useBulkMove`, `useBulkDelete`, `BulkResult`. `BrowsePage` still imports the old hooks until Task 8, so this task updates those imports too (see Step 2).

- [ ] **Step 1: Replace the types and hooks**

In `frontend/src/lib/types.ts`, replace the `BulkResult` interface with:

```ts
export interface BulkItemsResult {
  moved_folders?: number;
  moved_documents?: number;
  deleted_folders?: number;
  deleted_documents?: number;
  missing_folders: number[];
  missing_documents: string[];
}
```

In `frontend/src/hooks/useDocuments.ts`, change the type import to `import type { BulkItemsResult, Document } from "@/lib/types";` and replace `useBulkMove` and `useBulkDelete` with:

```ts
export interface BulkItems {
  folder_ids: number[];
  document_ids: string[];
}

export function useBulkMoveItems() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (body: BulkItems & { folder_id: number | null }) =>
      api.post<BulkItemsResult>("/api/bulk/move", body),
    onSuccess: invalidate,
  });
}

export function useBulkDeleteItems() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (body: BulkItems) => api.post<BulkItemsResult>("/api/bulk/delete", body),
    onSuccess: invalidate,
  });
}
```

- [ ] **Step 2: Keep `BrowsePage` compiling**

In `frontend/src/pages/BrowsePage.tsx`, change the import to `useBulkDeleteItems, useBulkMoveItems, useDeleteDocument, useDocuments` and the two mutations to:

```tsx
  const bulkMove = useBulkMoveItems();
  const bulkDelete = useBulkDeleteItems();
```

Change the two `mutate` calls in the `BulkActionBar` props to:

```tsx
            bulkMove.mutate(
              { folder_ids: [], document_ids: selectedIds, folder_id: folderId },
              { onSuccess: selection.clear, onError: reportBulk },
            );
```

```tsx
            bulkDelete.mutate(
              { folder_ids: [], document_ids: selectedIds },
              { onSuccess: selection.clear, onError: reportBulk },
            );
```

- [ ] **Step 3: Type check and test**

Run: `cd /opt/origami/frontend && npx tsc -b && npm test`
Expected: no type errors; all tests PASS.

- [ ] **Step 4: Commit**

```bash
cd /opt/origami && git add frontend/src/lib/types.ts frontend/src/hooks/useDocuments.ts frontend/src/pages/BrowsePage.tsx
git commit -m "feat: bulk item hooks calling /api/bulk"
```

---

### Task 7: Selectable folder tiles with subtree counts; FolderPicker disabled targets

**Files:**
- Modify: `frontend/src/components/FolderTiles.tsx`, `frontend/src/components/FolderPicker.tsx`
- Test: `frontend/src/components/FolderTiles.test.tsx`, `frontend/src/components/FolderPicker.test.tsx`

**Interfaces:**
- Consumes: `folderKey`, `subtreeDocumentCount` (Task 4).
- Produces:
  - `FolderTiles` new optional props: `selected?: ReadonlySet<string>`, `onToggleSelect?: (key: string, shift: boolean) => void`.
  - `FolderPicker` new optional prop: `disabledIds?: ReadonlySet<number>`.

- [ ] **Step 1: Update and add failing tests**

In `frontend/src/components/FolderTiles.test.tsx`, change the first test's expectation to subtree counts:

```tsx
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["📁Auto0 documents", "📁Bollette5 documents"]);
```

Append:

```tsx
it("checkbox toggles selection without opening the folder", async () => {
  const onOpen = vi.fn();
  const onToggleSelect = vi.fn();
  render(
    <FolderTiles folders={FOLDERS} parentId={null} onOpen={onOpen} selected={new Set(["f:3"])} onToggleSelect={onToggleSelect} />,
  );
  expect(screen.getByRole("checkbox", { name: "Select folder Auto" })).toBeChecked();
  await userEvent.click(screen.getByRole("checkbox", { name: "Select folder Bollette" }));
  expect(onToggleSelect).toHaveBeenCalledWith("f:1", false);
  expect(onOpen).not.toHaveBeenCalled();
});
```

Append to `frontend/src/components/FolderPicker.test.tsx`:

```tsx
it("disabled folders cannot be picked", async () => {
  const onChange = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <label htmlFor="pick2">Target</label>
      <FolderPicker id="pick2" value={null} onChange={onChange} disabledIds={new Set([1, 2])} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByLabelText("Target"));
  const bollette = await screen.findByRole("button", { name: "Bollette" });
  expect(bollette).toBeDisabled();
  expect(screen.getByRole("button", { name: "Assicurazioni" })).toBeEnabled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/components/FolderTiles.test.tsx src/components/FolderPicker.test.tsx`
Expected: FAIL (count 4 vs 5, missing checkbox, button not disabled).

- [ ] **Step 3: Implement `FolderTiles`**

Replace the `FolderTiles` function in `frontend/src/components/FolderTiles.tsx` (keep `Breadcrumb`). Update imports to:

```tsx
import { Fragment } from "react";
import { childrenOf, folderPath, subtreeDocumentCount } from "@/lib/folderTree";
import { folderKey } from "@/lib/selection";
import type { Folder } from "@/lib/types";
import { cn } from "@/lib/utils";
```

```tsx
export function FolderTiles({
  folders,
  parentId,
  onOpen,
  selected,
  onToggleSelect,
}: {
  folders: Folder[];
  parentId: number | null;
  onOpen: (id: number) => void;
  selected?: ReadonlySet<string>;
  onToggleSelect?: (key: string, shift: boolean) => void;
}) {
  const children = childrenOf(folders, parentId);
  if (children.length === 0) return null;
  const selecting = (selected?.size ?? 0) > 0;
  return (
    <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
      {children.map((f) => {
        const key = folderKey(f.id);
        const isSelected = selected?.has(key) ?? false;
        const count = subtreeDocumentCount(folders, f.id);
        return (
          <div
            key={f.id}
            className={cn(
              "group relative rounded-lg border bg-white hover:shadow",
              isSelected ? "border-brand-500 ring-1 ring-brand-500" : "border-zinc-200",
            )}
          >
            {onToggleSelect && (
              <input
                type="checkbox"
                aria-label={`Select folder ${f.name}`}
                checked={isSelected}
                onChange={() => {}}
                onClick={(e) => onToggleSelect(key, e.shiftKey)}
                className={cn(
                  "absolute top-2 left-2 z-10 h-4 w-4",
                  selecting || isSelected ? "opacity-100" : "opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100",
                )}
              />
            )}
            <button
              type="button"
              onClick={() => onOpen(f.id)}
              className={cn("flex w-full cursor-pointer items-center gap-2 p-3 text-left", onToggleSelect && "pl-7")}
            >
              <span aria-hidden="true" className="text-xl">
                📁
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{f.name}</span>
                <span className="block text-xs text-zinc-400">
                  {count} {count === 1 ? "document" : "documents"}
                </span>
              </span>
            </button>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Implement `FolderPicker.disabledIds`**

In `frontend/src/components/FolderPicker.tsx`, add `disabledIds` to the props (destructure and type `disabledIds?: ReadonlySet<number>;`). In the `entries.map` loop, compute `const disabled = disabledIds?.has(f.id) ?? false;` at the top of the callback (change the arrow body to a block returning the `<li>`), and on the item `<button>` add `disabled={disabled}` and extend the class:

```tsx
                  className={cn(
                    itemClass,
                    value === f.id && "bg-zinc-100 font-medium",
                    disabled && "cursor-not-allowed text-zinc-300 hover:bg-transparent",
                  )}
```

- [ ] **Step 5: Run tests**

Run: `cd /opt/origami/frontend && npx vitest run src/components/FolderTiles.test.tsx src/components/FolderPicker.test.tsx && npx tsc -b`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami && git add frontend/src/components/FolderTiles.tsx frontend/src/components/FolderTiles.test.tsx frontend/src/components/FolderPicker.tsx frontend/src/components/FolderPicker.test.tsx
git commit -m "feat: selectable folder tiles with subtree counts, disabled move targets"
```

---

### Task 8: Mixed bulk action bar and BrowsePage wiring

**Files:**
- Modify: `frontend/src/components/BulkActionBar.tsx`, `frontend/src/pages/BrowsePage.tsx`
- Test: `frontend/src/components/BulkActionBar.test.tsx`

**Interfaces:**
- Consumes: `itemsLabel` (Task 4), `folderKey`, `docKey`, `splitKeys`, `subtreeTotals`, `descendantIds`, `childrenOf`, `useBulkMoveItems`, `useBulkDeleteItems`, `FolderTiles` selection props, `FolderPicker.disabledIds`.
- Produces: `BulkActionBar` props `{ folderCount: number; documentCount: number; deleteFolderTotal: number; deleteDocumentTotal: number; disabledFolderIds: ReadonlySet<number>; onSelectAll; onMove(folderId: number | null); onDelete; onClear; busy? }` (`count` removed).

- [ ] **Step 1: Rewrite the bar tests**

Replace the `renderBar` helper and both tests in `frontend/src/components/BulkActionBar.test.tsx` (keep imports and fetch stub):

```tsx
function renderBar(overrides = {}) {
  const props = {
    folderCount: 0,
    documentCount: 3,
    deleteFolderTotal: 0,
    deleteDocumentTotal: 3,
    disabledFolderIds: new Set<number>(),
    onSelectAll: vi.fn(),
    onMove: vi.fn(),
    onDelete: vi.fn(),
    onClear: vi.fn(),
    ...overrides,
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BulkActionBar {...props} />
    </QueryClientProvider>,
  );
  return props;
}

it("confirms before deleting", async () => {
  const props = renderBar();
  expect(screen.getByText("3 documents selected")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(screen.getByRole("dialog", { name: "Delete 3 documents?" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
  expect(props.onDelete).toHaveBeenCalled();
});

it("labels a mixed selection and shows recursive delete totals", async () => {
  renderBar({ folderCount: 2, documentCount: 1, deleteFolderTotal: 3, deleteDocumentTotal: 7 });
  expect(screen.getByText("2 folders, 1 document selected")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(screen.getByRole("dialog", { name: "Delete 3 folders and 7 documents?" })).toBeInTheDocument();
  expect(screen.getByText(/subfolders and all their documents/i)).toBeInTheDocument();
});

it("moves to the root by default", async () => {
  const props = renderBar();
  await userEvent.click(screen.getByRole("button", { name: "Move…" }));
  expect(screen.getByRole("dialog", { name: "Move 3 documents" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Move here" }));
  expect(props.onMove).toHaveBeenCalledWith(null);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/components/BulkActionBar.test.tsx`
Expected: FAIL ("3 selected" label, "Delete 3" button).

- [ ] **Step 3: Implement `BulkActionBar`**

Replace `frontend/src/components/BulkActionBar.tsx` with:

```tsx
import { useState } from "react";
import { FolderPicker } from "@/components/FolderPicker";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { itemsLabel } from "@/lib/counts";

export function BulkActionBar({
  folderCount,
  documentCount,
  deleteFolderTotal,
  deleteDocumentTotal,
  disabledFolderIds,
  onSelectAll,
  onMove,
  onDelete,
  onClear,
  busy = false,
}: {
  folderCount: number;
  documentCount: number;
  deleteFolderTotal: number;
  deleteDocumentTotal: number;
  disabledFolderIds: ReadonlySet<number>;
  onSelectAll: () => void;
  onMove: (folderId: number | null) => void;
  onDelete: () => void;
  onClear: () => void;
  busy?: boolean;
}) {
  const [dialog, setDialog] = useState<"move" | "delete" | null>(null);
  const [target, setTarget] = useState<number | null>(null);

  return (
    <>
      <div className="sticky top-0 z-20 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 bg-white p-2 shadow">
        <span className="px-2 text-sm font-medium">{itemsLabel(folderCount, documentCount)} selected</span>
        <Button variant="ghost" onClick={onSelectAll}>
          Select all
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => setDialog("move")}>
          Move…
        </Button>
        <Button variant="destructive" disabled={busy} onClick={() => setDialog("delete")}>
          Delete
        </Button>
        <Button variant="ghost" className="ml-auto" onClick={onClear}>
          Clear
        </Button>
      </div>
      <Dialog
        open={dialog === "move"}
        onClose={() => setDialog(null)}
        title={`Move ${itemsLabel(folderCount, documentCount, " and ")}`}
      >
        <div className="space-y-3">
          <FolderPicker value={target} onChange={setTarget} disabledIds={disabledFolderIds} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                onMove(target);
                setDialog(null);
              }}
            >
              Move here
            </Button>
          </div>
        </div>
      </Dialog>
      <Dialog
        open={dialog === "delete"}
        onClose={() => setDialog(null)}
        title={`Delete ${itemsLabel(deleteFolderTotal, deleteDocumentTotal, " and ")}?`}
      >
        <p className="mb-4 text-sm text-zinc-600">
          {folderCount > 0
            ? "The folders, their subfolders and all their documents are removed permanently, with the files and their extracted text."
            : "The files and their extracted text are removed permanently."}
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setDialog(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onDelete();
              setDialog(null);
            }}
          >
            Delete permanently
          </Button>
        </div>
      </Dialog>
    </>
  );
}
```

- [ ] **Step 4: Wire `BrowsePage`**

In `frontend/src/pages/BrowsePage.tsx`:

Imports: change `import { childrenOf } from "@/lib/folderTree";` to `import { childrenOf, descendantIds, subtreeTotals } from "@/lib/folderTree";` and `import { shouldClearOnEscape } from "@/lib/selection";` to `import { docKey, folderKey, shouldClearOnEscape, splitKeys } from "@/lib/selection";`.

Replace the block from `const order = (docs ?? []).map((d) => d.id);` through `const selectedIds = [...selection.selected];` with:

```tsx
  const tileFolders = params.all ? [] : childrenOf(folders ?? [], params.folderId);
  const order = [...tileFolders.map((f) => folderKey(f.id)), ...(docs ?? []).map((d) => docKey(d.id))];
  const selection = useSelection(order, browseViewKey(params));
  const bulkMove = useBulkMoveItems();
  const bulkDelete = useBulkDeleteItems();
  const [bulkError, setBulkError] = useState<string | null>(null);
  const { folderIds, documentIds } = splitKeys(selection.selected);
  const selectedItems = { folder_ids: folderIds, document_ids: documentIds };
  const selectedTotals = subtreeTotals(folders ?? [], folderIds);
```

Replace the whole `<BulkActionBar ... />` element with:

```tsx
        <BulkActionBar
          folderCount={folderIds.length}
          documentCount={documentIds.length}
          deleteFolderTotal={selectedTotals.folders}
          deleteDocumentTotal={documentIds.length + selectedTotals.documents}
          disabledFolderIds={descendantIds(folders ?? [], folderIds)}
          busy={bulkMove.isPending || bulkDelete.isPending}
          onSelectAll={selection.selectAll}
          onClear={selection.clear}
          onMove={(folderId) => {
            setBulkError(null);
            bulkMove.mutate(
              { ...selectedItems, folder_id: folderId },
              { onSuccess: selection.clear, onError: reportBulk },
            );
          }}
          onDelete={() => {
            setBulkError(null);
            bulkDelete.mutate(selectedItems, { onSuccess: selection.clear, onError: reportBulk });
          }}
        />
```

(Documents listed on the page are in the open folder, which is never inside a selected tile folder, so adding `documentIds.length` does not double count.)

Pass selection to the tiles:

```tsx
          <FolderTiles
            folders={folders ?? []}
            parentId={params.folderId}
            onOpen={(id) => update({ folderId: id })}
            selected={selection.selected}
            onToggleSelect={selection.toggle}
          />
```

Change the `DocumentCard` selection props to:

```tsx
            selected={selection.selected.has(docKey(doc.id))}
            selecting={selection.selected.size > 0}
            onToggleSelect={(id, shift) => selection.toggle(docKey(id), shift)}
```

- [ ] **Step 5: Run all frontend checks**

Run: `cd /opt/origami/frontend && npm test && npx tsc -b && npm run lint`
Expected: all PASS, no type or lint errors.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami && git add frontend/src/components/BulkActionBar.tsx frontend/src/components/BulkActionBar.test.tsx frontend/src/pages/BrowsePage.tsx
git commit -m "feat: select, move and delete folders together with documents"
```

---

### Task 9: Sidebar tree follows navigation

**Files:**
- Modify: `frontend/src/components/FolderTree.tsx`, `frontend/src/hooks/useFolders.ts` (remove `useDeleteFolder`)
- Create: `frontend/src/components/FolderTree.test.tsx`

**Interfaces:**
- Consumes: `ancestorIds`, `subtreeTotals` (Task 4), `plural` (Task 4), `useBulkDeleteItems` (Task 6), `NewFolderDialog` (Task 5).
- Produces: `FolderTree` props unchanged: `{ selectedId: number | null; allSelected: boolean; onSelect(id: number | null); onSelectAll() }`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/FolderTree.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Folder } from "@/lib/types";
import { FolderTree } from "./FolderTree";

const FOLDERS: Folder[] = [
  { id: 1, name: "Bollette", parent_id: null, created_at: "", document_count: 2 },
  { id: 2, name: "2026", parent_id: 1, created_at: "", document_count: 3 },
  { id: 3, name: "Gennaio", parent_id: 2, created_at: "", document_count: 0 },
  { id: 4, name: "Auto", parent_id: null, created_at: "", document_count: 0 },
];

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async (path: string) =>
    path === "/api/folders"
      ? new Response(JSON.stringify(FOLDERS), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response(JSON.stringify({ deleted_folders: 3, deleted_documents: 5, missing_folders: [], missing_documents: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderTree(selectedId: number | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onSelect = vi.fn();
  const tree = (id: number | null) => (
    <QueryClientProvider client={qc}>
      <FolderTree selectedId={id} allSelected={false} onSelect={onSelect} onSelectAll={vi.fn()} />
    </QueryClientProvider>
  );
  const utils = render(tree(selectedId));
  return { onSelect, select: (id: number | null) => utils.rerender(tree(id)) };
}

it("shows only top-level folders when nothing is open", async () => {
  renderTree(null);
  expect(await screen.findByText("Bollette")).toBeInTheDocument();
  expect(screen.getByText("Auto")).toBeInTheDocument();
  expect(screen.queryByText("2026")).not.toBeInTheDocument();
});

it("expands the path of the open folder and collapses it when navigating away", async () => {
  const { select } = renderTree(3);
  expect(await screen.findByText("Gennaio")).toBeInTheDocument();
  expect(screen.getByText("2026")).toBeInTheDocument();
  select(4);
  await waitFor(() => expect(screen.queryByText("2026")).not.toBeInTheDocument());
});

it("arrow toggles without navigating; name click navigates", async () => {
  const { onSelect } = renderTree(null);
  await userEvent.click(await screen.findByRole("button", { name: "Expand Bollette" }));
  expect(screen.getByText("2026")).toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Bollette" }));
  expect(onSelect).toHaveBeenCalledWith(1);
});

it("deleting the open folder's ancestor deletes recursively and navigates to its parent", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const { onSelect } = renderTree(2);
  await screen.findByText("2026");
  const row = screen.getByRole("button", { name: "Bollette" }).parentElement!;
  await userEvent.click(within(row).getByTitle("Delete"));
  expect(confirm).toHaveBeenCalledWith(
    'Delete folder "Bollette" with 2 subfolders and 5 documents? This cannot be undone.',
  );
  await waitFor(() => expect(onSelect).toHaveBeenCalledWith(null));
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/bulk/delete",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ folder_ids: [1], document_ids: [] }) }),
  );
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/components/FolderTree.test.tsx`
Expected: FAIL (subfolders always rendered, no "Expand Bollette" label, old delete endpoint).

- [ ] **Step 3: Rewrite `FolderTree.tsx`**

Replace `frontend/src/components/FolderTree.tsx` with:

```tsx
import { useState } from "react";
import { NewFolderDialog } from "@/components/NewFolderDialog";
import { useBulkDeleteItems } from "@/hooks/useDocuments";
import { useFolders, useRenameFolder } from "@/hooks/useFolders";
import { ApiError } from "@/lib/api";
import { plural } from "@/lib/counts";
import { ancestorIds, buildFolderTree, subtreeTotals, type FolderNode } from "@/lib/folderTree";
import { cn } from "@/lib/utils";

function report(err: unknown) {
  window.alert(err instanceof ApiError ? err.message : "Operation failed");
}

interface NodeActions {
  selectedId: number | null;
  isExpanded: (id: number) => boolean;
  onToggle: (id: number) => void;
  onSelect: (id: number | null) => void;
  onNewSubfolder: (parentId: number) => void;
  onDelete: (node: FolderNode) => void;
}

function Node({ node, depth, actions }: { node: FolderNode; depth: number; actions: NodeActions }) {
  const rename = useRenameFolder();
  const open = actions.isExpanded(node.id);
  const hasChildren = node.children.length > 0;

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 rounded px-2 py-1 text-sm hover:bg-zinc-100",
          actions.selectedId === node.id && "bg-zinc-200 font-medium",
        )}
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <button
          onClick={() => actions.onToggle(node.id)}
          className="w-4 text-zinc-400"
          disabled={!hasChildren}
          aria-label={`${open ? "Collapse" : "Expand"} ${node.name}`}
          aria-expanded={hasChildren ? open : undefined}
        >
          {hasChildren ? (open ? "▾" : "▸") : "·"}
        </button>
        <button className="flex-1 truncate text-left" onClick={() => actions.onSelect(node.id)}>
          {node.name}
        </button>
        <span className="hidden gap-1 group-hover:flex">
          <button title="New subfolder" onClick={() => actions.onNewSubfolder(node.id)}>
            +
          </button>
          <button
            title="Rename"
            onClick={() => {
              const name = window.prompt("New name", node.name);
              if (name && name !== node.name) rename.mutate({ id: node.id, name }, { onError: report });
            }}
          >
            ✎
          </button>
          <button title="Delete" onClick={() => actions.onDelete(node)}>
            ×
          </button>
        </span>
      </div>
      {open && node.children.map((child) => <Node key={child.id} node={child} depth={depth + 1} actions={actions} />)}
    </div>
  );
}

export function FolderTree({
  selectedId,
  allSelected,
  onSelect,
  onSelectAll,
}: {
  selectedId: number | null;
  allSelected: boolean;
  onSelect: (id: number | null) => void;
  onSelectAll: () => void;
}) {
  const { data } = useFolders();
  const folders = data ?? [];
  const tree = buildFolderTree(folders);
  const bulkDelete = useBulkDeleteItems();
  const [newIn, setNewIn] = useState<number | null | undefined>(undefined); // undefined: dialog closed

  // the open folder's path is expanded; manual arrow toggles flip a node until the next navigation
  const onPath = ancestorIds(folders, selectedId);
  const [toggled, setToggled] = useState<Set<number>>(() => new Set());
  const [toggledFor, setToggledFor] = useState(selectedId);
  if (toggledFor !== selectedId) {
    setToggledFor(selectedId);
    setToggled(new Set());
  }

  const actions: NodeActions = {
    selectedId,
    isExpanded: (id) => onPath.has(id) !== toggled.has(id),
    onToggle: (id) =>
      setToggled((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
    onSelect,
    onNewSubfolder: (parentId) => setNewIn(parentId),
    onDelete: (node) => {
      const totals = subtreeTotals(folders, [node.id]);
      const subfolders = totals.folders - 1;
      const contents = [
        subfolders > 0 ? plural(subfolders, "subfolder") : null,
        totals.documents > 0 ? plural(totals.documents, "document") : null,
      ].filter(Boolean);
      const message = contents.length
        ? `Delete folder "${node.name}" with ${contents.join(" and ")}? This cannot be undone.`
        : `Delete folder "${node.name}"?`;
      if (!window.confirm(message)) return;
      bulkDelete.mutate(
        { folder_ids: [node.id], document_ids: [] },
        { onError: report, onSuccess: () => onPath.has(node.id) && onSelect(node.parent_id) },
      );
    },
  };

  return (
    <div className="[&_button]:cursor-pointer">
      <div className="mb-1 flex items-center justify-between px-2">
        <span className="text-xs font-semibold uppercase text-zinc-400">Folders</span>
        <button title="New folder" className="text-zinc-400 hover:text-zinc-700" onClick={() => setNewIn(null)}>
          +
        </button>
      </div>
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          allSelected && "bg-zinc-200 font-medium",
        )}
        onClick={onSelectAll}
      >
        All documents
      </button>
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          !allSelected && selectedId === null && "bg-zinc-200 font-medium",
        )}
        onClick={() => onSelect(null)}
      >
        Root
      </button>
      {tree.map((node) => (
        <Node key={node.id} node={node} depth={0} actions={actions} />
      ))}
      <NewFolderDialog open={newIn !== undefined} parentId={newIn ?? null} onClose={() => setNewIn(undefined)} />
    </div>
  );
}
```

Note: `Layout` passes `selectedId = null` when "All documents" is open, so the tree is collapsed there, and `onPath` is empty, so a sidebar delete never navigates away from "All documents".

- [ ] **Step 4: Remove the unused `useDeleteFolder`**

Delete the `useDeleteFolder` function from `frontend/src/hooks/useFolders.ts`. Confirm no other user: `grep -rn useDeleteFolder frontend/src` must print nothing.

- [ ] **Step 5: Run all frontend checks**

Run: `cd /opt/origami/frontend && npm test && npx tsc -b && npm run lint`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami && git add frontend/src/components/FolderTree.tsx frontend/src/components/FolderTree.test.tsx frontend/src/hooks/useFolders.ts
git commit -m "feat: sidebar tree expands along the open folder, recursive folder delete"
```

---

### Task 10: Full verification

- [ ] **Step 1: Run every suite**

Run: `cd /opt/origami/backend && uv run pytest -q` and `cd /opt/origami/frontend && npm test && npm run build && npm run lint`
Expected: all PASS, build succeeds.

- [ ] **Step 2: Manual check in the running app**

Restart the service (code changes need a restart, see deployment notes) or run the dev server, then check:
- `📁 New folder` (outline) sits left of Upload and creates in the open folder.
- Tiles show checkboxes on hover; shift-click selects a range across tiles and cards; the bar reads "N folders, M documents selected".
- Move dialog greys out selected folders and their subfolders; a name clash shows the 409 message.
- Delete dialog shows recursive totals; folders, files and empty directories are gone afterwards.
- Sidebar shows only top-level folders; opening a folder (tile, breadcrumb, sidebar) expands its path and collapses the previous one.
- Tile counts include subfolders.
