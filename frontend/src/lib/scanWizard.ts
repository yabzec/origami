import type { Document, ScanPageInfo } from "./types";

export type ScanPhase = "setup" | "ready" | "scanning" | "compiling" | "done";

export interface ScanState {
  phase: ScanPhase;
  sessionId: number | null;
  languages: string;
  pages: ScanPageInfo[];
  error: { code: string; message: string } | null;
  document: Document | null;
}

export const initialScanState: ScanState = {
  phase: "setup",
  sessionId: null,
  languages: "ita+eng",
  pages: [],
  error: null,
  document: null,
};

export type ScanAction =
  | { type: "SET_LANGUAGES"; languages: string }
  | { type: "SESSION_STARTED"; sessionId: number }
  | { type: "SCAN_STARTED" }
  | { type: "PAGE_SCANNED"; page: ScanPageInfo }
  | { type: "SCAN_FAILED"; code: string; message: string }
  | { type: "PAGE_DELETED"; pageId: number }
  | { type: "PAGES_REORDERED"; pages: ScanPageInfo[] }
  | { type: "COMPILE_STARTED" }
  | { type: "COMPILED"; document: Document }
  | { type: "COMPILE_FAILED"; code: string; message: string }
  | { type: "DISMISS_ERROR" }
  | { type: "RESET" };

export function scanWizardReducer(state: ScanState, action: ScanAction): ScanState {
  switch (action.type) {
    case "SET_LANGUAGES":
      return { ...state, languages: action.languages };
    case "SESSION_STARTED":
      return { ...state, phase: "ready", sessionId: action.sessionId, pages: [], error: null };
    case "SCAN_STARTED":
      return state.phase === "ready" ? { ...state, phase: "scanning", error: null } : state;
    case "PAGE_SCANNED":
      return { ...state, phase: "ready", pages: [...state.pages, action.page] };
    case "SCAN_FAILED":
      return { ...state, phase: "ready", error: { code: action.code, message: action.message } };
    case "PAGE_DELETED": {
      const remaining = state.pages.filter((p) => p.id !== action.pageId);
      return {
        ...state,
        pages: remaining.map((p, index) => ({ ...p, page_number: index + 1 })),
      };
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
