import { useRef, useState, type DragEvent } from "react";
import { useSearchParams } from "react-router";
import { DocumentCard } from "@/components/DocumentCard";
import { UploadDialog } from "@/components/UploadDialog";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useDeleteDocument, useDocuments } from "@/hooks/useDocuments";
import { useTags } from "@/hooks/useTags";
import { browseSearch, parseSort, SORT_OPTIONS } from "@/lib/sorting";
import { DOC_TYPES } from "@/lib/types";

export function BrowsePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const folderId = searchParams.get("folder") ? Number(searchParams.get("folder")) : null;
  const sort = parseSort(searchParams.get("sort"));
  const [tagId, setTagId] = useState<number | null>(null);
  const [docType, setDocType] = useState<string | null>(null);
  const { data: docs, isLoading } = useDocuments({ folderId, tagId, docType, sort });
  const { data: tags } = useTags();
  const deleteDoc = useDeleteDocument();

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
      <div className="mb-4 flex items-center gap-3">
        <h2 className="flex-1 text-lg font-semibold">Documents</h2>
        <Select
          className="w-52"
          aria-label="Order by"
          value={sort}
          onChange={(e) => setSearchParams(new URLSearchParams(browseSearch(folderId, parseSort(e.target.value))))}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
        <Select
          className="w-40"
          value={tagId ?? ""}
          onChange={(e) => setTagId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All tags</option>
          {(tags ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>
        <Select className="w-32" value={docType ?? ""} onChange={(e) => setDocType(e.target.value || null)}>
          <option value="">All types</option>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
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
      {isLoading && <p className="text-zinc-400">Loading…</p>}
      {docs && docs.length === 0 && <p className="text-zinc-400">No documents here yet — upload or scan one.</p>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(docs ?? []).map((doc) => (
          <DocumentCard key={doc.id} doc={doc} onDelete={(id) => deleteDoc.mutate(id)} />
        ))}
      </div>
      <UploadDialog
        file={pendingFile}
        open={pendingFile !== null}
        onClose={() => setPendingFile(null)}
        initialFolderId={folderId}
      />
    </div>
  );
}
