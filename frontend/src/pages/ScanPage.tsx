import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { PageCarousel } from "@/components/scan/PageCarousel";
import { ScanPreview } from "@/components/scan/ScanPreview";
import { emptyScanForm, ScanSidebar, type ScanFormFields } from "@/components/scan/ScanSidebar";
import { ScanToolbar } from "@/components/scan/ScanToolbar";
import { useLeaveGuard } from "@/hooks/useLeaveGuard";
import { useTags } from "@/hooks/useTags";
import { api, ApiError, getToken } from "@/lib/api";
import { DEFAULT_OCR_LANGUAGES } from "@/lib/ocrLanguages";
import { initialScanState, scannerMessage, scanWizardReducer, shouldBlockLeave } from "@/lib/scanWizard";
import type { Document, ScanDevice, ScanPageInfo, ScanStatus } from "@/lib/types";

const LEAVE_MESSAGE = "You have unsaved scanned pages. Leave and discard them?";

function errorInfo(err: unknown): { code: string; message: string } {
  return err instanceof ApiError
    ? { code: err.code, message: err.message }
    : { code: "unknown", message: "Unexpected error" };
}

export function ScanPage() {
  const [state, dispatch] = useReducer(scanWizardReducer, initialScanState);
  const { data: status } = useQuery({
    queryKey: ["scan-status"],
    queryFn: () => api.get<ScanStatus>("/api/scan/status"),
    refetchInterval: 10_000,
  });
  const { data: deviceData } = useQuery({
    queryKey: ["scan-devices"],
    queryFn: () => api.get<{ devices: ScanDevice[]; default: string | null }>("/api/scan/devices"),
  });
  const { data: tags } = useTags();

  const [device, setDevice] = useState<string | null>(null);
  const chosenDevice = device ?? deviceData?.default ?? null;
  const [languages, setLanguages] = useState(DEFAULT_OCR_LANGUAGES);
  const [ocrEnabled, setOcrEnabled] = useState(true);
  const [fields, setFields] = useState<ScanFormFields>(emptyScanForm);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const startInFlight = useRef(false); // StrictMode re-runs effects: create exactly one session
  // Async handlers capture the session at call time and drop results once it changed (Discard/reset).
  const sessionIdRef = useRef(state.sessionId);
  useEffect(() => {
    sessionIdRef.current = state.sessionId;
  }, [state.sessionId]);
  const isCurrent = (sessionId: number | null) => sessionIdRef.current === sessionId;

  const clearPreview = useCallback(() => {
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, []);

  useEffect(() => {
    if (state.phase !== "starting" || state.error !== null || startInFlight.current) return;
    startInFlight.current = true;
    api
      .post<{ id: number }>("/api/scan/sessions", {
        ocr_languages: languages,
        ocr_enabled: ocrEnabled,
        device: chosenDevice,
      })
      .then((session) => dispatch({ type: "SESSION_STARTED", sessionId: session.id }))
      .catch((err) => dispatch({ type: "SESSION_FAILED", ...errorInfo(err) }))
      .finally(() => {
        startInFlight.current = false;
      });
    // languages/ocrEnabled/device are only defaults here; compile and page scans send the current values
  }, [state.phase, state.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const leaveSession = useCallback(() => {
    if (state.sessionId !== null) api.del(`/api/scan/sessions/${state.sessionId}`).catch(() => {});
  }, [state.sessionId]);
  useLeaveGuard(shouldBlockLeave(state), LEAVE_MESSAGE, leaveSession);

  const preview = async () => {
    const sessionId = state.sessionId;
    setPreviewing(true);
    try {
      const resp = await fetch("/api/scan/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken() ?? ""}` },
        body: JSON.stringify({ device: chosenDevice }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => null);
        if (!isCurrent(sessionId)) return;
        dispatch({
          type: "SCAN_FAILED",
          code: data?.error?.code ?? "unknown",
          message: data?.error?.message ?? "Preview failed",
        });
        return;
      }
      const blob = await resp.blob();
      if (!isCurrent(sessionId)) return;
      const url = URL.createObjectURL(blob);
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return url;
      });
    } finally {
      setPreviewing(false);
    }
  };

  const scanPage = async () => {
    const sessionId = state.sessionId;
    clearPreview();
    dispatch({ type: "SCAN_STARTED" });
    try {
      const page = await api.post<ScanPageInfo>(`/api/scan/sessions/${sessionId}/pages`, {
        device: chosenDevice,
      });
      if (!isCurrent(sessionId)) return;
      dispatch({ type: "PAGE_SCANNED", page: { id: page.id, page_number: page.page_number } });
    } catch (err) {
      if (!isCurrent(sessionId)) return;
      dispatch({ type: "SCAN_FAILED", ...errorInfo(err) });
    }
  };

  const selectPage = (pageId: number) => {
    clearPreview();
    dispatch({ type: "SELECT_PAGE", pageId });
  };

  const deletePage = async (pageId: number) => {
    const sessionId = state.sessionId;
    try {
      await api.del(`/api/scan/pages/${pageId}`);
      if (!isCurrent(sessionId)) return;
      dispatch({ type: "PAGE_DELETED", pageId });
    } catch (err) {
      if (!isCurrent(sessionId)) return;
      dispatch({ type: "SCAN_FAILED", ...errorInfo(err) });
    }
  };

  const finish = async () => {
    dispatch({ type: "COMPILE_STARTED" });
    try {
      const doc = await api.post<Document>(`/api/scan/sessions/${state.sessionId}/compile`, {
        title: fields.title.trim(),
        description: fields.description,
        document_date: fields.documentDate || null,
        folder_id: fields.folderId,
        tag_ids: fields.tagIds,
        ocr_languages: languages,
        ocr_enabled: ocrEnabled,
      });
      clearPreview();
      dispatch({ type: "COMPILED", document: doc });
    } catch (err) {
      dispatch({ type: "COMPILE_FAILED", ...errorInfo(err) });
    }
  };

  const startOver = () => {
    clearPreview();
    setFields(emptyScanForm());
    sessionIdRef.current = null; // drop in-flight results of the old session right away
    dispatch({ type: "RESET" }); // phase "starting" → effect creates a new session
  };

  const discard = async () => {
    if (state.pages.length > 0 && !window.confirm("Discard all scanned pages?")) return;
    if (state.sessionId !== null) await api.del(`/api/scan/sessions/${state.sessionId}`).catch(() => {});
    startOver();
  };

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Scan</h2>
        <div className="ml-auto">
          <ScanToolbar
            status={status}
            devices={deviceData?.devices ?? []}
            device={chosenDevice}
            onDeviceChange={setDevice}
            languages={languages}
            onLanguagesChange={setLanguages}
            ocrEnabled={ocrEnabled}
            onOcrEnabledChange={setOcrEnabled}
          />
        </div>
      </div>

      {state.error && (
        <div className="flex items-center justify-between rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <span>{scannerMessage(state.error.code, state.error.message)}</span>
          <button onClick={() => dispatch({ type: "DISMISS_ERROR" })}>
            {state.phase === "starting" ? "Retry" : "×"}
          </button>
        </div>
      )}

      {state.phase === "done" && state.document ? (
        <div className="space-y-3">
          <p>
            Document created:{" "}
            <Link className="font-medium underline" to={`/documents/${state.document.id}`}>
              {state.document.title}
            </Link>{" "}
            (processing in the background)
          </p>
          <Button onClick={startOver}>Scan another document</Button>
        </div>
      ) : (
        <div className="flex flex-col gap-6 lg:flex-row">
          <div className="min-w-0 flex-1 space-y-3">
            <ScanPreview
              pageId={state.selectedPageId}
              previewUrl={previewUrl}
              scanning={state.phase === "scanning"}
            />
            <PageCarousel
              pages={state.pages}
              selectedPageId={state.selectedPageId}
              disabled={state.phase !== "ready"}
              onSelect={selectPage}
              onDelete={deletePage}
            />
            {state.phase === "starting" && !state.error && (
              <p className="text-sm text-zinc-500">Starting scan session…</p>
            )}
          </div>
          <ScanSidebar
            fields={fields}
            onChange={(patch) => setFields((prev) => ({ ...prev, ...patch }))}
            tags={tags ?? []}
            phase={state.phase}
            pageCount={state.pages.length}
            previewing={previewing}
            onPreview={preview}
            onScan={scanPage}
            onFinish={finish}
            onDiscard={discard}
          />
        </div>
      )}
    </div>
  );
}
