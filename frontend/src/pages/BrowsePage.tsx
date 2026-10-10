import { useEffect, useRef, useState, type DragEvent } from "react";
import { useSearchParams } from "react-router";
import { BulkActionBar } from "@/components/BulkActionBar";
import { DocFilters, FilterField } from "@/components/DocFilters";
import { DocumentCard } from "@/components/DocumentCard";
import { BatchUploadDialog } from "@/components/BatchUploadDialog";
import { NewFolderDialog } from "@/components/NewFolderDialog";
import { UploadDialog } from "@/components/UploadDialog";
import { Breadcrumb, FolderTiles } from "@/components/FolderTiles";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useBulkDeleteItems, useBulkMoveItems, useDeleteDocument, useDocuments } from "@/hooks/useDocuments";
import { useFolders } from "@/hooks/useFolders";
import { useSelection } from "@/hooks/useSelection";
import { useTags } from "@/hooks/useTags";
import { ApiError } from "@/lib/api";
import {
  browseQuery,
  browseViewKey,
  invalidDateRange,
  parseBrowseParams,
  type BrowseParams,
} from "@/lib/browseParams";
import type { PickedFile } from "@/lib/batchUpload";
import { pickedFromDataTransfer, pickedFromInput } from "@/lib/dropEntries";
import { childrenOf } from "@/lib/folderTree";
import { parseSort, SORT_OPTIONS } from "@/lib/sorting";
import { shouldClearOnEscape } from "@/lib/selection";

export function BrowsePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const params = parseBrowseParams(searchParams);
  const update = (patch: Partial<BrowseParams>) =>
    setSearchParams(
      new URLSearchParams(browseQuery({ ...params, ...patch }).slice(1)),
    );
  const badRange = invalidDateRange(params);
  const { data: folders } = useFolders();
  const {
    data: docs,
    isLoading,
    isError,
    error,
  } = useDocuments(
    {
      folder: params.all ? null : (params.folderId ?? "root"),
      tagId: params.tagId,
      docType: params.docType,
      dateFrom: params.dateFrom,
      dateTo: params.dateTo,
      sort: params.sort,
    },
    !badRange,
  );
  const { data: tags } = useTags();
  const deleteDoc = useDeleteDocument();

  const order = (docs ?? []).map((d) => d.id);
  const selection = useSelection(order, browseViewKey(params));
  const bulkMove = useBulkMoveItems();
  const bulkDelete = useBulkDeleteItems();
  const [bulkError, setBulkError] = useState<string | null>(null);
  const selectedIds = [...selection.selected];
  const reportBulk = (err: unknown) => setBulkError(err instanceof ApiError ? err.message : "Bulk action failed");

  const { clear } = selection;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && shouldClearOnEscape()) clear();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clear]);

  const fileInput = useRef<HTMLInputElement>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const folderInput = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<PickedFile[] | null>(null);
  const [uploadMenu, setUploadMenu] = useState(false);
  const [newFolder, setNewFolder] = useState(false);

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", ""); // not in React's input typings
  }, []);

  useEffect(() => {
    if (!uploadMenu) return;
    const close = () => setUploadMenu(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onClick = (e: MouseEvent) => {
      if (!(e.target as Element).closest("[data-upload-menu]")) close();
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [uploadMenu]);

  const receive = (files: PickedFile[]) => {
    if (files.length === 1 && !files[0].relativePath.includes("/")) setPendingFile(files[0].file);
    else if (files.length > 0) setPicked(files);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    setBulkError(null);
    pickedFromDataTransfer(e.dataTransfer)
      .then(receive)
      .catch(() => setBulkError("Could not read the dropped files"));
  };

  return (
    <div
      className="relative min-h-full p-6"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center border-4 border-dashed border-zinc-400 bg-white/80 text-lg text-zinc-600">
          Drop to upload
        </div>
      )}
      <div className="mb-4 flex items-center gap-3">
        <h2 className="flex-1 text-lg font-semibold">
          {params.all ? "All documents" : "Documents"}
        </h2>
        <Button variant="outline" onClick={() => setNewFolder(true)}>
          <span aria-hidden="true" className="mr-1.5">
            📁
          </span>
          New folder
        </Button>
        <div className="relative" data-upload-menu>
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
      </div>
      <div className="mb-4">
        <DocFilters value={params} tags={tags ?? []} onChange={update}>
          <FilterField label="Order by">
            <Select
              className="w-52"
              value={params.sort}
              onChange={(e) => update({ sort: parseSort(e.target.value) })}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FilterField>
        </DocFilters>
      </div>
      {badRange && (
        <p className="mb-3 text-sm text-red-600">
          “From” date is after “To” date.
        </p>
      )}
      {isError && (
        <p className="mb-3 text-sm text-red-600">
          {error instanceof ApiError
            ? error.message
            : "Could not load documents"}
        </p>
      )}
      {selection.selected.size > 0 && (
        <BulkActionBar
          count={selection.selected.size}
          busy={bulkMove.isPending || bulkDelete.isPending}
          onSelectAll={selection.selectAll}
          onClear={selection.clear}
          onMove={(folderId) => {
            setBulkError(null);
            bulkMove.mutate(
              { folder_ids: [], document_ids: selectedIds, folder_id: folderId },
              { onSuccess: selection.clear, onError: reportBulk },
            );
          }}
          onDelete={() => {
            setBulkError(null);
            bulkDelete.mutate(
              { folder_ids: [], document_ids: selectedIds },
              { onSuccess: selection.clear, onError: reportBulk },
            );
          }}
        />
      )}
      {bulkError && <p className="mb-3 text-sm text-red-600">{bulkError}</p>}
      {!params.all && (
        <>
          <Breadcrumb
            folders={folders ?? []}
            folderId={params.folderId}
            onNavigate={(id) => update({ folderId: id })}
          />
          <FolderTiles
            folders={folders ?? []}
            parentId={params.folderId}
            onOpen={(id) => update({ folderId: id })}
          />
        </>
      )}
      {isLoading && <p className="text-zinc-400">Loading…</p>}
      {docs &&
        docs.length === 0 &&
        (params.all ||
          childrenOf(folders ?? [], params.folderId).length === 0) && (
          <p className="text-zinc-400">
            No documents here yet — upload or scan one.
          </p>
        )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(docs ?? []).map((doc) => (
          <DocumentCard
            key={doc.id}
            doc={doc}
            onDelete={(id) => deleteDoc.mutate(id)}
            selected={selection.selected.has(doc.id)}
            selecting={selection.selected.size > 0}
            onToggleSelect={selection.toggle}
          />
        ))}
      </div>
      <NewFolderDialog
        open={newFolder}
        parentId={params.all ? null : params.folderId}
        onClose={() => setNewFolder(false)}
      />
      <UploadDialog
        file={pendingFile}
        open={pendingFile !== null}
        onClose={() => setPendingFile(null)}
        initialFolderId={params.all ? null : params.folderId}
      />
      {picked && (
        <BatchUploadDialog
          key={picked.map((p) => p.relativePath).join("|")}
          picked={picked}
          open
          onClose={() => setPicked(null)}
          initialFolderId={params.all ? null : params.folderId}
        />
      )}
    </div>
  );
}
