import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FolderPicker } from "@/components/FolderPicker";
import { Textarea } from "@/components/ui/textarea";
import { STATUS_VARIANTS } from "@/components/DocumentCard";
import { ProcessingOptions } from "@/components/ProcessingOptions";
import { TagInput } from "@/components/TagInput";
import { useNow } from "@/hooks/useNow";
import { api, ApiError, fileUrl } from "@/lib/api";
import { browseQuery, DEFAULT_BROWSE } from "@/lib/browseParams";
import { isAiDescription, nextDescription } from "@/lib/description";
import { defaultProcessing, processingFromDocument, processingPayload, type ProcessingValues } from "@/lib/processing";
import { processingRetryMessage, shouldPollDocument } from "@/lib/retry";
import { canRetranslate, languageLabel, textVariants, translationNote, type TextVariant } from "@/lib/translation";
import type { Document, DocumentText } from "@/lib/types";
import { viewerKind } from "@/lib/viewer";

function Viewer({ doc }: { doc: Document }) {
  const kind = viewerKind(doc);
  if (doc.status !== "ready" && kind !== "video")
    return <div className="flex h-96 items-center justify-center text-zinc-400">Processing…</div>;
  const src = fileUrl(doc.id, { preview: doc.preview_path !== null });
  if (kind === "pdf") return <iframe title="preview" src={src} className="h-[75vh] w-full rounded border" />;
  if (kind === "image") return <img src={src} alt={doc.title} className="max-h-[75vh] rounded border" />;
  if (kind === "video") return <video controls src={src} className="max-h-[75vh] w-full rounded border" />;
  return <TextView doc={doc} />;
}

function TextView({ doc }: { doc: Document }) {
  const now = useNow();
  const note = translationNote(doc, now);
  const variants = textVariants(doc);
  const [variant, setVariant] = useState<TextVariant>("content");
  const active = variants.includes(variant) ? variant : "content";
  const { data } = useQuery({
    queryKey: ["document-text", doc.id, active],
    queryFn: () => api.get<DocumentText>(`/api/documents/${doc.id}/text?variant=${active}`),
  });
  if (!data) return <p className="text-zinc-400">Loading…</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {data.detected_language && <Badge variant="blue">Detected: {data.detected_language.toUpperCase()}</Badge>}
        {variants.length > 1 && (
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-300 text-sm">
            {variants.map((v) => (
              <button
                key={v}
                onClick={() => setVariant(v)}
                className={active === v ? "bg-zinc-900 px-3 py-1 text-white" : "px-3 py-1 hover:bg-zinc-100"}
              >
                {v === "content" ? "Original" : languageLabel(data.translation_language)}
              </button>
            ))}
          </div>
        )}
        {note && <span className="text-xs text-amber-700">{note}</span>}
      </div>
      {data.summary && (
        <div className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
          <span className="font-medium">Summary: </span>
          {data.summary}
        </div>
      )}
      {data.chunks.map((chunk) => (
        <div key={chunk.chunk_index}>
          {chunk.page_number != null && (
            <p className="mb-1 text-xs font-semibold text-zinc-400">Page {chunk.page_number}</p>
          )}
          <p className="whitespace-pre-wrap text-sm">{chunk.content}</p>
        </div>
      ))}
      {data.chunks.length === 0 && <p className="text-zinc-400">No extracted text.</p>}
    </div>
  );
}

export function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: doc } = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<Document>(`/api/documents/${id}`),
    refetchInterval: (q) => (q.state.data && shouldPollDocument(q.state.data) ? 4000 : false),
  });

  const [tab, setTab] = useState<"preview" | "text">("preview");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [documentDate, setDocumentDate] = useState("");
  const [processing, setProcessing] = useState<ProcessingValues>(defaultProcessing);
  const lastStatus = useRef<string | null>(null);
  const lastTranslation = useRef<string | null>(null);
  const now = useNow();

  const hydratedForDocId = useRef<string | null>(null);
  const serverDescription = useRef<string | null>(null);

  useEffect(() => {
    if (!doc) return;
    if (hydratedForDocId.current !== doc.id) {
      setTitle(doc.title);
      setDescription(doc.description);
      setFolderId(doc.folder_id);
      setTagIds(doc.tags.map((t) => t.id));
      setDocumentDate(doc.document_date);
      setProcessing(processingFromDocument(doc));
      hydratedForDocId.current = doc.id;
    } else if (serverDescription.current !== doc.description) {
      // pipeline filled (or re-process cleared) the description: follow it unless the user edited the field
      const previous = serverDescription.current;
      setDescription((current) => nextDescription(current, previous, doc.description));
    }
    serverDescription.current = doc.description;
  }, [doc]);

  useEffect(() => {
    if (!doc) return;
    // re-process finished, or the translation job finished: refetch extracted/translated text
    const processed = lastStatus.current && lastStatus.current !== "ready" && doc.status === "ready";
    const translated = lastTranslation.current === "pending" && doc.translation_status === "done";
    if (processed || translated) qc.invalidateQueries({ queryKey: ["document-text", doc.id] });
    lastStatus.current = doc.status;
    lastTranslation.current = doc.translation_status;
  }, [doc, qc]);

  const save = useMutation({
    mutationFn: () =>
      api.patch<Document>(`/api/documents/${id}`, {
        title,
        // only send the description if the user changed it, so a just-arrived AI text is not erased
        ...(description !== serverDescription.current ? { description } : {}),
        folder_id: folderId,
        tag_ids: tagIds,
        document_date: documentDate || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/documents/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      navigate("/");
    },
  });

  const reprocess = useMutation({
    mutationFn: () =>
      api.post<Document>(`/api/documents/${id}/reprocess`, processingPayload(processing)),
    onSuccess: (updated) => {
      qc.setQueryData(["document", id], updated);
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const retranslate = useMutation({
    mutationFn: () => api.post<Document>(`/api/documents/${id}/retranslate`),
    onSuccess: (updated) => {
      qc.setQueryData(["document", id], updated);
      qc.invalidateQueries({ queryKey: ["document-text", id] });
    },
  });
  const confirmReprocess = () => {
    const base = "Re-run OCR and AI processing? Extracted text, summary and translation will be replaced.";
    const pdfNote =
      doc && (doc.doc_type === "pdf" || doc.doc_type === "scan") && processing.ocrEnabled && doc.ocr_applied !== false
        ? " The PDF is rebuilt from page images."
        : "";
    if (window.confirm(base + pdfNote)) reprocess.mutate();
  };

  if (!doc) return <div className="p-8 text-zinc-400">Loading…</div>;
  const retryMessage = processingRetryMessage(doc, now);

  return (
    <div className="flex gap-6 p-6">
      <div className="flex-1">
        <div className="mb-3 flex items-center gap-3">
          <button
            type="button"
            aria-label="Exit"
            title="Back to folder"
            onClick={() => navigate(`/${browseQuery({ ...DEFAULT_BROWSE, folderId: doc.folder_id })}`)}
            className="cursor-pointer text-2xl leading-none text-zinc-400 hover:text-zinc-700"
          >
            ×
          </button>
          <h2 className="flex-1 truncate text-lg font-semibold">{doc.title}</h2>
          {doc.file_path && (
            <a
              href={fileUrl(doc.id, { download: true })}
              className="inline-flex h-8 items-center rounded-md border border-zinc-300 px-3 text-sm hover:bg-zinc-100"
            >
              Download
            </a>
          )}
          <Badge variant={STATUS_VARIANTS[doc.status]}>{doc.status}</Badge>
        </div>
        {doc.status === "failed" && (
          <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            Processing failed: {doc.error_message}
          </div>
        )}
        {retryMessage && (
          <div className="mb-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            {retryMessage}
          </div>
        )}
        <div className="mb-3 flex gap-2 border-b border-zinc-200">
          {(["preview", "text"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={
                tab === t ? "border-b-2 border-zinc-800 px-3 py-1 font-medium" : "px-3 py-1 text-zinc-500"
              }
            >
              {t === "preview" ? "Preview" : "Text"}
            </button>
          ))}
        </div>
        {tab === "preview" ? <Viewer doc={doc} /> : <TextView doc={doc} />}
      </div>
      <aside className="w-72 space-y-3">
        <div>
          <Label htmlFor="d-title">Title</Label>
          <Input id="d-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="d-desc">Description</Label>
          <Textarea id="d-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
          {isAiDescription(description, doc.summary) && <p className="mt-1 text-xs text-zinc-400">AI generated</p>}
        </div>
        <div>
          <Label htmlFor="d-date">Document date</Label>
          <Input id="d-date" type="date" value={documentDate} onChange={(e) => setDocumentDate(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="d-folder">Folder</Label>
          <FolderPicker id="d-folder" value={folderId} onChange={setFolderId} />
        </div>
        <div>
          <Label htmlFor="d-tags">Tags</Label>
          <TagInput id="d-tags" value={tagIds} onChange={setTagIds} />
        </div>
        <Button className="w-full" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
        <Button
          variant="destructive"
          className="w-full"
          onClick={() => window.confirm(`Delete "${doc.title}"?`) && remove.mutate()}
        >
          Delete
        </Button>
        {doc.doc_type !== "video" && (
          <div className="space-y-2 border-t border-zinc-200 pt-3">
            <ProcessingOptions idPrefix="d" value={processing} onChange={setProcessing} />
            <Button
              variant="outline"
              className="w-full"
              disabled={reprocess.isPending || doc.status === "pending" || doc.status === "processing"}
              onClick={confirmReprocess}
            >
              {reprocess.isPending ? "Starting…" : "Re-process"}
            </Button>
            {reprocess.isError && (
              <p className="text-xs text-red-600">
                {reprocess.error instanceof ApiError ? reprocess.error.message : "Re-process failed"}
              </p>
            )}
            {doc.translatable && (
              <>
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={retranslate.isPending || !canRetranslate(doc)}
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
          </div>
        )}
      </aside>
    </div>
  );
}
