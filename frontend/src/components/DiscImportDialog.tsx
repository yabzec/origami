import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { DiscStudyCard } from "@/components/DiscStudyCard";
import { ProcessingOptions } from "@/components/ProcessingOptions";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import type { PickedFile } from "@/lib/batchUpload";
import {
  batchForm,
  canImport,
  commitBody,
  discFiles,
  discProcessing,
  manualFiles,
  mergeDrafts,
  reportsFor,
  uploadBatches,
  type StagedFile,
  type StudyDraft,
} from "@/lib/discImport";
import type { ProcessingValues } from "@/lib/processing";
import type { DiscPreview, Document } from "@/lib/types";

type Phase =
  | { kind: "uploading"; percent: number }
  | { kind: "preview" }
  | { kind: "importing" }
  | { kind: "done"; documents: Document[] }
  | { kind: "failed"; message: string };

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function ManualPickers({ onAdd }: { onAdd: (files: StagedFile[]) => void }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div>
        <Label htmlFor="disc-manual-images">Images (DICOM, JPEG, PNG)</Label>
        <input
          id="disc-manual-images"
          type="file"
          multiple
          accept=".dcm,.jpg,.jpeg,.png,application/dicom"
          className="block text-sm"
          onChange={(e) => {
            onAdd(manualFiles(Array.from(e.target.files ?? [])));
            e.target.value = "";
          }}
        />
      </div>
      <div>
        <Label htmlFor="disc-manual-report">Report (PDF, optional)</Label>
        <input
          id="disc-manual-report"
          type="file"
          accept=".pdf,application/pdf"
          className="block text-sm"
          onChange={(e) => {
            onAdd(manualFiles(Array.from(e.target.files ?? [])));
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

export function DiscImportDialog({
  picked,
  onClose,
  initialFolderId,
}: {
  picked: PickedFile[];
  onClose: () => void;
  initialFolderId: number | null;
}) {
  const qc = useQueryClient();
  const [importId, setImportId] = useState<string | null>(null);
  const [preview, setPreview] = useState<DiscPreview | null>(null);
  const previewRef = useRef<DiscPreview | null>(null);
  const [drafts, setDrafts] = useState<StudyDraft[]>([]);
  const [processing, setProcessing] = useState<ProcessingValues>(discProcessing);
  const [phase, setPhase] = useState<Phase>({ kind: "uploading", percent: 0 });
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const started = useRef(false);
  const committed = useRef(false);
  const importIdRef = useRef<string | null>(null);
  const closed = useRef(false);
  const importing = useRef(false);

  const send = async (id: string, files: StagedFile[]): Promise<DiscPreview> => {
    const total = files.reduce((n, f) => n + f.file.size, 0) || 1;
    let done = 0;
    let latest: DiscPreview | null = null;
    setPhase({ kind: "uploading", percent: 0 });
    for (const batch of uploadBatches(files)) {
      const size = batch.reduce((n, f) => n + f.file.size, 0);
      latest = await api.upload<DiscPreview>(`/api/disc-imports/${id}/files`, batchForm(batch), (p) =>
        setPhase({ kind: "uploading", percent: Math.round(((done + (size * p) / 100) / total) * 100) }),
      );
      done += size;
    }
    return latest ?? api.get<DiscPreview>(`/api/disc-imports/${id}`);
  };

  const apply = (next: DiscPreview) => {
    setDrafts((current) => mergeDrafts(current, previewRef.current, next, initialFolderId));
    previewRef.current = next;
    setPreview(next);
    setPhase({ kind: "preview" });
  };

  const start = async () => {
    try {
      const { id } = await api.post<{ id: string }>("/api/disc-imports");
      importIdRef.current = id;
      if (closed.current) {
        api.del(`/api/disc-imports/${id}`).catch(() => {});
        return;
      }
      setImportId(id);
      apply(await send(id, discFiles(picked)));
    } catch (err) {
      setPhase({ kind: "failed", message: message(err, "Upload failed") });
    }
  };

  useEffect(() => {
    if (started.current) return; // StrictMode runs effects twice in development
    started.current = true;
    void start();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const addFiles = async (files: StagedFile[]) => {
    if (!importId || files.length === 0) return;
    setAdding(false);
    try {
      apply(await send(importId, files));
    } catch (err) {
      setPhase({ kind: "preview" });
      setError(message(err, "Upload failed"));
    }
  };

  const close = () => {
    if (importing.current) return; // the commit is using the staged files
    closed.current = true;
    if (importIdRef.current && !committed.current) api.del(`/api/disc-imports/${importIdRef.current}`).catch(() => {});
    onClose();
  };

  const retry = () => {
    if (importIdRef.current) api.del(`/api/disc-imports/${importIdRef.current}`).catch(() => {});
    importIdRef.current = null;
    setImportId(null);
    setPreview(null);
    previewRef.current = null;
    setDrafts([]);
    void start();
  };

  const runImport = async () => {
    if (!importId || !preview) return;
    setError(null);
    importing.current = true;
    setPhase({ kind: "importing" });
    try {
      const result = await api.post<{ documents: Document[] }>(
        `/api/disc-imports/${importId}/commit`,
        commitBody(drafts, preview, processing),
      );
      committed.current = true;
      importing.current = false; // the staging now belongs to the jobs; the done view can be closed
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["folders"] });
      setPhase({ kind: "done", documents: result.documents });
    } catch (err) {
      importing.current = false;
      setPhase({ kind: "preview" });
      setError(message(err, "Import failed"));
    }
  };

  return (
    <Dialog open onClose={close} title="Import X-ray disc" className="max-h-[90vh] !max-w-3xl overflow-y-auto">
      {phase.kind === "uploading" && (
        <div className="space-y-2">
          <p className="text-sm text-zinc-600">Uploading the disc files…</p>
          <div className="h-2 rounded bg-zinc-200">
            <div className="h-2 rounded bg-zinc-800" style={{ width: `${phase.percent}%` }} />
          </div>
        </div>
      )}
      {phase.kind === "failed" && (
        <div className="space-y-3">
          <p className="text-sm text-red-600">{phase.message}</p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button onClick={retry}>Retry</Button>
          </div>
        </div>
      )}
      {phase.kind === "done" && (
        <div className="space-y-3">
          <p className="text-sm">Imported:</p>
          <ul className="list-disc pl-5 text-sm">
            {phase.documents.map((d) => (
              <li key={d.id}>
                <Link to={`/documents/${d.id}`} className="underline" onClick={onClose}>
                  {d.title}
                </Link>
              </li>
            ))}
          </ul>
          <div className="flex justify-end">
            <Button onClick={onClose}>Close</Button>
          </div>
        </div>
      )}
      {(phase.kind === "preview" || phase.kind === "importing") && preview && importId && (
        <div className="space-y-4">
          {preview.warnings.map((w) => (
            <p key={w} className="rounded border border-amber-200 bg-amber-50 p-2 text-sm text-amber-800">
              {w}
            </p>
          ))}
          {preview.no_study ? (
            <div className="space-y-3">
              <p className="text-sm">No X-ray study found on this disc. Add the files manually.</p>
              <ManualPickers onAdd={addFiles} />
            </div>
          ) : (
            <>
              {preview.studies.map((study) => {
                const draft = drafts.find((d) => d.key === study.key);
                return draft ? (
                  <DiscStudyCard
                    key={study.key}
                    importId={importId}
                    study={study}
                    reports={reportsFor(study, preview.reports)}
                    draft={draft}
                    onChange={(next) => setDrafts((all) => all.map((d) => (d.key === next.key ? next : d)))}
                  />
                ) : null;
              })}
              {adding ? (
                <ManualPickers onAdd={addFiles} />
              ) : (
                <Button variant="outline" onClick={() => setAdding(true)}>
                  Add files
                </Button>
              )}
            </>
          )}
          <ProcessingOptions idPrefix="disc" value={processing} onChange={setProcessing} />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={close} disabled={phase.kind === "importing"}>
              Cancel
            </Button>
            <Button onClick={runImport} disabled={phase.kind === "importing" || !canImport(drafts)}>
              {phase.kind === "importing" ? "Importing…" : "Import"}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
