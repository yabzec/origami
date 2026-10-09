import type { Document, ScanPageInfo } from "./types";

export type ScanPhase = "starting" | "ready" | "scanning" | "compiling" | "done";

export interface ScanState {
  phase: ScanPhase;
  sessionId: number | null;
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  error: { code: string; message: string } | null;
  document: Document | null;
}

export const initialScanState: ScanState = {
  phase: "starting",
  sessionId: null,
  pages: [],
  selectedPageId: null,
  error: null,
  document: null,
};

export type ScanAction =
  | { type: "SESSION_STARTED"; sessionId: number }
  | { type: "SESSION_FAILED"; code: string; message: string }
  | { type: "SCAN_STARTED" }
  | { type: "PAGE_SCANNED"; page: ScanPageInfo }
  | { type: "SCAN_FAILED"; code: string; message: string }
  | { type: "SELECT_PAGE"; pageId: number }
  | { type: "PAGE_DELETED"; pageId: number }
  | { type: "PAGES_REORDERED"; pages: ScanPageInfo[] }
  | { type: "COMPILE_STARTED" }
  | { type: "COMPILED"; document: Document }
  | { type: "COMPILE_FAILED"; code: string; message: string }
  | { type: "DISMISS_ERROR" }
  | { type: "RESET" };

/** A failure with no active session (stale result after Discard/reset) only reports the error. */
function failed(state: ScanState, action: { code: string; message: string }): ScanState {
  const error = { code: action.code, message: action.message };
  return state.sessionId === null ? { ...state, error } : { ...state, phase: "ready", error };
}

export function scanWizardReducer(state: ScanState, action: ScanAction): ScanState {
  switch (action.type) {
    case "SESSION_STARTED":
      return { ...initialScanState, phase: "ready", sessionId: action.sessionId };
    case "SESSION_FAILED":
      return { ...state, phase: "starting", error: { code: action.code, message: action.message } };
    case "SCAN_STARTED":
      return state.phase === "ready" ? { ...state, phase: "scanning", error: null } : state;
    case "PAGE_SCANNED":
      return { ...state, phase: "ready", pages: [...state.pages, action.page], selectedPageId: action.page.id };
    case "SCAN_FAILED":
      return failed(state, action);
    case "SELECT_PAGE":
      return { ...state, selectedPageId: action.pageId };
    case "PAGE_DELETED": {
      const remaining = state.pages
        .filter((p) => p.id !== action.pageId)
        .map((p, index) => ({ ...p, page_number: index + 1 }));
      const selectedPageId =
        state.selectedPageId === action.pageId ? (remaining.at(-1)?.id ?? null) : state.selectedPageId;
      return { ...state, pages: remaining, selectedPageId };
    }
    case "PAGES_REORDERED":
      return { ...state, pages: action.pages };
    case "COMPILE_STARTED":
      return { ...state, phase: "compiling", error: null };
    case "COMPILED":
      return { ...state, phase: "done", document: action.document };
    case "COMPILE_FAILED":
      return failed(state, action);
    case "DISMISS_ERROR":
      return { ...state, error: null };
    case "RESET":
      return initialScanState;
  }
}

/** Unsaved scanned pages exist: leaving the page would discard them (not while they are being saved). */
export function shouldBlockLeave(state: ScanState): boolean {
  return state.pages.length > 0 && state.phase !== "done" && state.phase !== "compiling";
}

export const SCANNER_MESSAGES: Record<string, string> = {
  scanner_offline: "Scanner not found — check power and USB connection.",
  scanner_busy: "The scanner is busy with another operation. Try again in a moment.",
  scanner_jam: "Paper jam detected — clear the scanner and retry.",
  cover_open: "The scanner cover is open — close it and retry.",
  scanner_timeout: "The scan timed out — try power-cycling the scanner.",
};

const AGENT_OFFLINE_MESSAGE =
  "Scanner not reachable — make sure the Origami Agent is running and the scanner is on, then use Search local scanners.";

export function scannerMessage(code: string, fallback: string, device?: string | null): string {
  if (code === "scanner_offline" && device?.startsWith("agent:")) return AGENT_OFFLINE_MESSAGE;
  return SCANNER_MESSAGES[code] ?? fallback;
}

/** New page order with `pageId` at `toIndex` (clamped), renumbered from 1. */
export function movePage(pages: ScanPageInfo[], pageId: number, toIndex: number): ScanPageInfo[] {
  const from = pages.findIndex((p) => p.id === pageId);
  if (from === -1) return pages;
  const rest = pages.filter((p) => p.id !== pageId);
  const target = Math.max(0, Math.min(toIndex, rest.length));
  rest.splice(target, 0, pages[from]);
  return rest.map((p, index) => ({ ...p, page_number: index + 1 }));
}
