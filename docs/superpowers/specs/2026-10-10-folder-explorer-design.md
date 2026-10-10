# Folder explorer improvements — design

Date: 2026-10-10
Branch: `feat/folder-explorer`

## Goals

1. Folders can be selected in the explorer together with documents, then moved or deleted in bulk.
2. A "New folder" button next to Upload creates a folder in the current folder.
3. The sidebar tree shows only top-level folders; clicking a folder opens it in the explorer and expands it to show its subfolders.
4. Folder tiles show the number of documents in the whole subtree, not only direct children.

## Decisions

| Topic | Decision |
|---|---|
| Deleting a non-empty folder | Recursive: the folder, all subfolders and all documents inside are deleted. The confirm dialog shows totals. |
| Selecting folders | Same checkbox pattern as document cards. Folders and documents share one selection and one bulk action bar. |
| Move name conflict | The whole operation is rejected; nothing moves. Selected folders and their descendants are disabled as move targets. |
| Backend shape | One mixed bulk endpoint pair, `/api/bulk/move` and `/api/bulk/delete`, each a single transaction. |
| New folder button | `outline` variant, label `📁 New folder` (no plus sign), opens a dialog. |
| Sidebar expansion | Follows navigation: the open folder and its ancestors are expanded, everything else collapsed. An arrow toggles a node manually without navigating. |
| Folder count | Computed in the frontend from the `/api/folders` list. No backend change. |

## Backend

### New module `backend/app/api/bulk.py`

Router prefix `/api/bulk`, depends on `get_current_user` like the other routers. Registered in `app/main.py`.

#### `POST /api/bulk/move`

Body:

```json
{ "folder_ids": [1, 2], "document_ids": ["uuid", "..."], "folder_id": 7 }
```

`folder_id: null` means root. Both id lists default to empty.

Steps, all inside one `storage_errors()` + `disk_transaction(session, storage) as moves` block:

1. `lock_tree(session)`, then `session.expire_all()`.
2. Validate the destination exists (404 `not_found`).
3. Drop missing folder ids (reported as `missing_folders`).
4. Normalise the folder selection: drop any selected folder that lies inside another selected folder (it moves with its ancestor).
5. Reject with 409 `folder_cycle` if the destination is a selected folder or a descendant of one (`is_descendant`).
6. Reject with 409 `duplicate_folder` if a selected folder's name clashes at the destination, either with an existing child (`disk_name_taken`, excluding the folder itself) or with another selected folder being moved there. Also apply `check_reserved_name` for moves to root. The message names the clashing folder.
7. Reparent each folder through a helper `reparent_folder(session, moves, folder, new_parent_id)` extracted from `update_folder`: set `parent_id`, flush, `moves.move_dir(old_dir, new_dir)`, and rewrite the `file_path` prefix of documents under the old dir. `update_folder` is refactored to use the same helper for its rename/move path, so there is one code path.
8. Documents: drop selected documents whose folder is inside a moved folder (they moved with it). For the rest, use the existing `bulk_move` logic: `lock_documents`, set `folder_id` and `updated_at`, `relocate_document`. Shared code is extracted from `documents.bulk_move` rather than duplicated.

Any validation error happens before any change, so a rejection leaves the DB and disk untouched. Disk moves that already happened are undone by `disk_transaction` if a later step fails.

Response:

```json
{ "moved_folders": 2, "moved_documents": 5, "missing_folders": [], "missing_documents": [] }
```

#### `POST /api/bulk/delete`

Body: `{ "folder_ids": [...], "document_ids": [...] }`.

1. `lock_tree(session)`, `session.expire_all()`.
2. Drop missing ids (reported).
3. Collect every descendant folder id of the selected folders (breadth-first over `Folder.parent_id`), giving the full folder set.
4. Collect all documents whose `folder_id` is in that set, merge with selected document ids, de-duplicate.
5. `lock_documents` on the merged ids, then `_delete_documents` (cancels queued jobs; chunks, tags and translation segments cascade via FK).
6. Compute each folder's relative dir (`folder_rel_dir`) before deleting rows, then delete folder rows deepest-first.
7. Commit.
8. After commit: `storage.delete_document_files` for each document, then `storage.remove_dir` for each folder dir, deepest-first.

`remove_dir` stays non-recursive (`rmdir`). An untracked file left on disk keeps its directory and logs a warning. The code never calls `rmtree` on user storage.

Response:

```json
{ "deleted_folders": 3, "deleted_documents": 42, "missing_folders": [], "missing_documents": [] }
```

The existing `/api/documents/bulk/*` and `DELETE /api/folders/{id}` endpoints stay unchanged for other callers.

### Backend tests — `backend/tests/test_bulk_api.py`

- Mixed move of folders and documents; disk paths and `file_path` values updated.
- Move into a selected folder or its descendant → 409 `folder_cycle`, nothing changed.
- Name conflict at destination (existing child, and two selected folders with the same name) → 409 `duplicate_folder`, nothing changed on disk or in DB.
- Nested selection (folder plus its subfolder, folder plus a document inside it) is de-duplicated.
- Recursive delete removes folder rows, document rows, files and directories.
- Queued jobs of deleted documents are cancelled.
- Missing ids are reported, not fatal.
- `PATCH /api/folders/{id}` tests still pass after the `reparent_folder` extraction.

## Frontend

### Selection

`useSelection` stays generic. Keys become typed strings:

- `f:<id>` for folders, `d:<uuid>` for documents.

New helpers in `lib/selection.ts`: `folderKey(id)`, `docKey(id)`, `splitKeys(keys) → { folderIds: number[], documentIds: string[] }`.

`BrowsePage` builds `order` as the visible folder tiles (in display order) followed by the documents, so shift-range and "Select all" cover both. In the "All documents" view no tiles are shown, so only documents can be selected there.

### Folder tiles

- Each tile becomes a `div` wrapper (relative, `group`) containing the open `button` and a checkbox, so the checkbox is not nested in a button.
- Checkbox behaviour and styling match `DocumentCard`: visible on hover, always visible while a selection exists, brand ring when selected. Click toggles (shift = range).
- Clicking anywhere else on the tile opens the folder, as today.
- New props: `selected: ReadonlySet<string>`, `selecting: boolean`, `onToggleSelect(key, shift)`.

### Folder counts

New `subtreeDocumentCount(folders, id)` in `lib/folderTree.ts` sums `document_count` over the folder and all descendants. Tiles show this value ("42 documents").

### Bulk action bar

- Props `count` → `folderCount`, `documentCount`, `deleteDocumentTotal` and `disabledFolderIds`. `BrowsePage` computes all four from the selection and the folder list and passes them in; the bar does no tree logic.
- Label: "2 folders, 5 documents selected"; a single kind omits the other part ("5 documents selected").
- Delete dialog title: "Delete 2 folders and 47 documents?". The document total is the selected documents plus all documents in the selected folders' subtrees, without double counting. The body warns that subfolders and their contents are removed permanently.
- Move dialog: `FolderPicker` gets a `disabledIds` prop; selected folders and their descendants are shown greyed and cannot be picked.
- New hooks `useBulkMoveItems` and `useBulkDeleteItems` in `hooks/useDocuments.ts`, calling `/api/bulk/move` and `/api/bulk/delete`, invalidating the documents and folders queries.
- Server errors (409) appear in the existing `bulkError` line.
- If the currently open folder (or an ancestor) is deleted, navigate to the nearest surviving parent.

### New folder button and dialog

- `<Button variant="outline">📁 New folder</Button>` placed left of the Upload button.
- New component `components/NewFolderDialog.tsx`: `Dialog` with a name `Input`, Create and Cancel buttons; Enter submits. Props: `open`, `parentId`, `onClose`, `onCreated?(folder)`.
- Creates the folder in the current folder; root in the "All documents" view.
- Errors (duplicate, reserved name, empty name) show inline; the dialog stays open.
- The sidebar "New folder" (+ at the header) and "New subfolder" (+ on a node) use the same dialog instead of `window.prompt`. Rename keeps its prompt.

### Sidebar tree

- `FolderTree` already receives `selectedId` from the URL via `Layout`, so it follows tile, breadcrumb and sidebar navigation.
- New `ancestorIds(folders, id): Set<number>` in `lib/folderTree.ts` (built on `folderPath`); includes `id` itself.
- `FolderTree` holds `toggled: Set<number>` for manual arrow toggles; it resets when `selectedId` changes.
- A node is expanded when `onPath(node) XOR toggled.has(node.id)`. The arrow can collapse a node on the current path or expand one off it.
- `Node` loses its own `useState(true)`; it receives `expanded` and `onToggle`.
- Only top-level folders render initially; children render only when their parent is expanded.
- Arrow ▸/▾ when the folder has children, `·` otherwise. Arrow click only toggles; it does not navigate.
- Name click navigates; the previous branch collapses and the new path expands.
- "All documents" and "Root" entries are unchanged.
- Hover actions: "+" opens `NewFolderDialog` for a subfolder; ✎ rename unchanged; × uses `/api/bulk/delete` with a confirm that shows subtree totals ("Delete folder "X" with 3 subfolders and 42 documents?"), then navigates to the parent if the open folder was inside the deleted one.
- Pages with no folder in the URL (document, search, chat) show the tree fully collapsed.

### Frontend tests (vitest)

- `lib/selection`: `folderKey`, `docKey`, `splitKeys`.
- `lib/folderTree`: `subtreeDocumentCount`, `ancestorIds`.
- `FolderTiles`: checkbox toggles selection without opening the folder; tile click opens.
- `BulkActionBar`: mixed labels, delete totals, disabled move targets.
- `FolderPicker`: `disabledIds` cannot be picked.
- `NewFolderDialog`: submit creates in the given parent; server error shown inline.
- `FolderTree` (new test file): only roots initially; deep `selectedId` expands its path; changing path collapses the old branch; arrow toggle does not call `onSelect`.

## Out of scope

- Drag and drop of folders or documents onto folders.
- Merging folders on name conflict.
- Undo or trash for deletes.
- Selecting folders in the "All documents" view.
