import { useEffect, useRef, useState, type DragEvent } from "react";
import { useSearchParams } from "react-router";
import { BulkActionBar } from "@/components/BulkActionBar";
import { DocumentCard } from "@/components/DocumentCard";
import { UploadDialog } from "@/components/UploadDialog";
import { Breadcrumb, FolderTiles } from "@/components/FolderTiles";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useBulkDelete, useBulkMove, useDeleteDocument, useDocuments } from "@/hooks/useDocuments";
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
import { childrenOf } from "@/lib/folderTree";
import { parseSort, SORT_OPTIONS } from "@/lib/sorting";
import { shouldClearOnEscape } from "@/lib/selection";
import { DOC_TYPES } from "@/lib/types";

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
  const bulkMove = useBulkMove();
  const bulkDelete = useBulkDelete();
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

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) setPendingFile(file);
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
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="flex-1 text-lg font-semibold">
          {params.all ? "All documents" : "Documents"}
        </h2>
        <Select
          className="w-52"
          aria-label="Order by"
          value={params.sort}
          onChange={(e) => update({ sort: parseSort(e.target.value) })}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
        <Select
          className="w-40"
          value={params.tagId ?? ""}
          onChange={(e) =>
            update({ tagId: e.target.value ? Number(e.target.value) : null })
          }
        >
          <option value="">All tags</option>
          {(tags ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>
        <Select
          className="w-32"
          value={params.docType ?? ""}
          onChange={(e) => update({ docType: e.target.value || null })}
        >
          <option value="">All types</option>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
        <Input
          type="date"
          aria-label="From date"
          className="w-40"
          value={params.dateFrom ?? ""}
          onChange={(e) => update({ dateFrom: e.target.value || null })}
        />
        <Input
          type="date"
          aria-label="To date"
          className="w-40"
          value={params.dateTo ?? ""}
          onChange={(e) => update({ dateTo: e.target.value || null })}
        />
        {(params.dateFrom || params.dateTo) && (
          <Button
            variant="ghost"
            onClick={() => update({ dateFrom: null, dateTo: null })}
          >
            Clear dates
          </Button>
        )}
        <Button onClick={() => fileInput.current?.click()}>Upload</Button>
        <input
          ref={fileInput}
          type="file"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) setPendingFile(file);
            e.target.value = "";
          }}
        />
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
            bulkMove.mutate({ ids: selectedIds, folder_id: folderId }, { onSuccess: selection.clear, onError: reportBulk });
          }}
          onDelete={() => {
            setBulkError(null);
            bulkDelete.mutate(selectedIds, { onSuccess: selection.clear, onError: reportBulk });
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
      <UploadDialog
        file={pendingFile}
        open={pendingFile !== null}
        onClose={() => setPendingFile(null)}
        initialFolderId={params.all ? null : params.folderId}
      />
    </div>
  );
}
