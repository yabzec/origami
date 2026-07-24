import { useReducer, useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useFolders } from "@/hooks/useFolders";
import { useTags } from "@/hooks/useTags";
import { usePreviewImage } from "@/hooks/usePreviewImage";
import { api, ApiError, getToken } from "@/lib/api";
import { scanDeviceHint } from "@/lib/scanDevices";
import {
  initialScanState,
  scannerMessage,
  scanWizardReducer,
} from "@/lib/scanWizard";
import type { Document, ScanDevice, ScanPageInfo, ScanStatus } from "@/lib/types";

function Thumbnail({
  page,
  onDelete,
  onMove,
  isFirst,
  isLast,
}: {
  page: ScanPageInfo;
  onDelete: () => void;
  onMove: (direction: -1 | 1) => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  const url = usePreviewImage(page.id);
  return (
    <div className="w-36 rounded border border-zinc-200 bg-white p-2">
      {url ? (
        <img src={url} alt={`Page ${page.page_number}`} className="h-40 w-full rounded object-cover" />
      ) : (
        <div className="flex h-40 items-center justify-center text-zinc-300">…</div>
      )}
      <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
        <span>p. {page.page_number}</span>
        <span className="flex gap-1">
          <button disabled={isFirst} onClick={() => onMove(-1)} title="Move up/left">
            ←
          </button>
          <button disabled={isLast} onClick={() => onMove(1)} title="Move down/right">
            →
          </button>
          <button onClick={onDelete} title="Delete page" className="text-red-500">
            ×
          </button>
        </span>
      </div>
    </div>
  );
}

export function ScanPage() {
  const [state, dispatch] = useReducer(scanWizardReducer, initialScanState);
  const { data: status } = useQuery({
    queryKey: ["scan-status"],
    queryFn: () => api.get<ScanStatus>("/api/scan/status"),
    refetchInterval: 10_000,
  });
  const { data: folders } = useFolders();
  const { data: tags } = useTags();
  const { data: deviceData } = useQuery({
    queryKey: ["scan-devices"],
    queryFn: () => api.get<{ devices: ScanDevice[]; default: string | null }>("/api/scan/devices"),
  });
  const devices = deviceData?.devices ?? [];
  const [device, setDevice] = useState<string | null>(null);
  const chosenDevice = device ?? deviceData?.default ?? null;

  const [ocrEnabled, setOcrEnabled] = useState(true);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const [compileOpen, setCompileOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [tagIds, setTagIds] = useState<number[]>([]);

  const fail = (kind: "SCAN_FAILED" | "COMPILE_FAILED") => (err: unknown) => {
    const code = err instanceof ApiError ? err.code : "unknown";
    const message = err instanceof ApiError ? err.message : "Unexpected error";
    dispatch({ type: kind, code, message });
  };

  const startSession = async () => {
    try {
      const session = await api.post<{ id: number }>("/api/scan/sessions", {
        ocr_languages: state.languages,
        ocr_enabled: ocrEnabled,
        device: chosenDevice,
      });
      dispatch({ type: "SESSION_STARTED", sessionId: session.id });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const scanPage = async () => {
    dispatch({ type: "SCAN_STARTED" });
    try {
      const page = await api.post<ScanPageInfo & { preview_url: string }>(
        `/api/scan/sessions/${state.sessionId}/pages`,
        {},
      );
      dispatch({ type: "PAGE_SCANNED", page: { id: page.id, page_number: page.page_number } });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const deletePage = async (pageId: number) => {
    try {
      await api.del(`/api/scan/pages/${pageId}`);
      dispatch({ type: "PAGE_DELETED", pageId });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const movePage = async (index: number, direction: -1 | 1) => {
    const order = state.pages.map((p) => p.id);
    const target = index + direction;
    [order[index], order[target]] = [order[target], order[index]];
    try {
      const resp = await api.post<{ pages: ScanPageInfo[] }>(
        `/api/scan/sessions/${state.sessionId}/reorder`,
        { page_ids: order },
      );
      dispatch({ type: "PAGES_REORDERED", pages: resp.pages });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const compile = async () => {
    setCompileOpen(false);
    dispatch({ type: "COMPILE_STARTED" });
    try {
      const doc = await api.post<Document>(`/api/scan/sessions/${state.sessionId}/compile`, {
        title,
        folder_id: folderId,
        tag_ids: tagIds,
      });
      dispatch({ type: "COMPILED", document: doc });
    } catch (err) {
      fail("COMPILE_FAILED")(err);
    }
  };

  const cancel = async () => {
    if (state.sessionId !== null) await api.del(`/api/scan/sessions/${state.sessionId}`).catch(() => {});
    dispatch({ type: "RESET" });
  };

  return (
    <div className="p-6">
      <h2 className="mb-2 text-lg font-semibold">Scan</h2>
      {status && (
        <p className="mb-4 text-sm">
          Scanner:{" "}
          {status.available ? (
            <span className="text-green-700">available{status.busy ? " (busy)" : ""}</span>
          ) : (
            <span className="text-red-600">not detected — check power and USB</span>
          )}
        </p>
      )}
      {state.error && (
        <div className="mb-4 flex items-center justify-between rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <span>{scannerMessage(state.error.code, state.error.message)}</span>
          <button onClick={() => dispatch({ type: "DISMISS_ERROR" })}>×</button>
        </div>
      )}

      {state.phase === "setup" && (
        <div className="max-w-sm space-y-3">
          {ocrEnabled && (
            <div>
              <Label htmlFor="scan-lang">OCR language</Label>
              <Select
                id="scan-lang"
                value={state.languages}
                onChange={(e) => dispatch({ type: "SET_LANGUAGES", languages: e.target.value })}
              >
                <option value="ita+eng">Italian + English</option>
                <option value="ita">Italian</option>
                <option value="eng">English</option>
              </Select>
            </div>
          )}
          {scanDeviceHint(devices) === "none" && (
            <p className="text-sm text-red-600">No scanner detected — check power and USB.</p>
          )}
          {scanDeviceHint(devices) === "single" && (
            <p className="text-sm text-zinc-600">Scanner: {devices[0].name}</p>
          )}
          {scanDeviceHint(devices) === "multiple" && (
            <div>
              <Label htmlFor="scan-device">Scanner</Label>
              <Select
                id="scan-device"
                value={chosenDevice ?? ""}
                onChange={(e) => setDevice(e.target.value || null)}
              >
                {devices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={ocrEnabled} onChange={(e) => setOcrEnabled(e.target.checked)} />
            Run OCR (extract text)
          </label>
          <div>
            <Button
              variant="outline"
              disabled={previewing}
              onClick={async () => {
                setPreviewing(true);
                try {
                  const resp = await fetch("/api/scan/preview", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken() ?? ""}` },
                    body: JSON.stringify({ device: chosenDevice }),
                  });
                  if (resp.ok) {
                    if (previewUrl) URL.revokeObjectURL(previewUrl);
                    setPreviewUrl(URL.createObjectURL(await resp.blob()));
                  }
                } finally {
                  setPreviewing(false);
                }
              }}
            >
              {previewing ? "Previewing…" : "Preview"}
            </Button>
            {previewUrl && (
              <div className="mt-2">
                <img src={previewUrl} alt="scan preview" className="max-h-64 rounded border" />
                <button
                  className="mt-1 block text-xs text-zinc-500"
                  onClick={() => {
                    URL.revokeObjectURL(previewUrl);
                    setPreviewUrl(null);
                  }}
                >
                  clear preview
                </button>
              </div>
            )}
          </div>
          <Button onClick={startSession}>Start scan session</Button>
        </div>
      )}

      {(state.phase === "ready" || state.phase === "scanning" || state.phase === "compiling") && (
        <div>
          <div className="mb-4 flex flex-wrap gap-3">
            {state.pages.map((p, index) => (
              <Thumbnail
                key={p.id}
                page={p}
                isFirst={index === 0}
                isLast={index === state.pages.length - 1}
                onDelete={() => deletePage(p.id)}
                onMove={(direction) => movePage(index, direction)}
              />
            ))}
            {state.pages.length === 0 && <p className="text-zinc-400">No pages yet — scan the first one.</p>}
          </div>
          <div className="flex gap-2">
            <Button onClick={scanPage} disabled={state.phase !== "ready"}>
              {state.phase === "scanning" ? "Scanning…" : state.pages.length === 0 ? "Scan first page" : "Scan next page"}
            </Button>
            <Button
              variant="outline"
              disabled={state.pages.length === 0 || state.phase !== "ready"}
              onClick={() => {
                setTitle("");
                setCompileOpen(true);
              }}
            >
              Finish & compile
            </Button>
            <Button variant="ghost" onClick={cancel}>
              Cancel
            </Button>
          </div>
          {state.phase === "compiling" && <p className="mt-3 text-sm text-zinc-500">Compiling document…</p>}
        </div>
      )}

      {state.phase === "done" && state.document && (
        <div className="space-y-3">
          <p>
            Document created:{" "}
            <Link className="font-medium underline" to={`/documents/${state.document.id}`}>
              {state.document.title}
            </Link>{" "}
            (processing in the background)
          </p>
          <Button onClick={() => dispatch({ type: "RESET" })}>Scan another document</Button>
        </div>
      )}

      <Dialog open={compileOpen} onClose={() => setCompileOpen(false)} title="Compile document">
        <div className="space-y-3">
          <div>
            <Label htmlFor="c-title">Title</Label>
            <Input id="c-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="c-folder">Folder</Label>
            <Select
              id="c-folder"
              value={folderId ?? ""}
              onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">(root)</option>
              {(folders ?? []).map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Tags</Label>
            <div className="flex flex-wrap gap-2">
              {(tags ?? []).map((tag) => (
                <label key={tag.id} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={tagIds.includes(tag.id)}
                    onChange={(e) =>
                      setTagIds(e.target.checked ? [...tagIds, tag.id] : tagIds.filter((x) => x !== tag.id))
                    }
                  />
                  {tag.name}
                </label>
              ))}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setCompileOpen(false)}>
              Back
            </Button>
            <Button onClick={compile} disabled={!title.trim()}>
              Compile
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
