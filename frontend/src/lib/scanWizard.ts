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
      return { ...state, phase: "ready", error: { code: action.code, message: action.message } };
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
      return { ...state, phase: "ready", error: { code: action.code, message: action.message } };
    case "DISMISS_ERROR":
      return { ...state, error: null };
    case "RESET":
      return initialScanState;
  }
}

/** Unsaved scanned pages exist: leaving the page would discard them. */
export function shouldBlockLeave(state: ScanState): boolean {
  return state.pages.length > 0 && state.phase !== "done";
}

export const SCANNER_MESSAGES: Record<string, string> = {
  scanner_offline: "Scanner not found — check power and USB connection.",
  scanner_busy: "The scanner is busy with another operation. Try again in a moment.",
  scanner_jam: "Paper jam detected — clear the scanner and retry.",
  cover_open: "The scanner cover is open — close it and retry.",
  scanner_timeout: "The scan timed out — try power-cycling the scanner.",
};

export function scannerMessage(code: string, fallback: string): string {
  return SCANNER_MESSAGES[code] ?? fallback;
}
