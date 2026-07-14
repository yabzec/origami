import { describe, expect, it } from "vitest";
import {
  initialScanState,
  scannerMessage,
  scanWizardReducer,
  type ScanState,
} from "./scanWizard";
import type { Document } from "./types";

const page = (id: number, page_number: number) => ({ id, page_number });

function reduceAll(actions: Parameters<typeof scanWizardReducer>[1][], from = initialScanState): ScanState {
  return actions.reduce(scanWizardReducer, from);
}

describe("scanWizardReducer", () => {
  it("walks the happy path: setup → ready → scanning → ready → compiling → done", () => {
    let state = reduceAll([
      { type: "SET_LANGUAGES", languages: "ita" },
      { type: "SESSION_STARTED", sessionId: 5 },
    ]);
    expect(state.phase).toBe("ready");
    expect(state.languages).toBe("ita");

    state = scanWizardReducer(state, { type: "SCAN_STARTED" });
    expect(state.phase).toBe("scanning");
    state = scanWizardReducer(state, { type: "PAGE_SCANNED", page: page(1, 1) });
    expect(state.phase).toBe("ready");
    expect(state.pages).toHaveLength(1);

    state = reduceAll([{ type: "COMPILE_STARTED" }, { type: "COMPILED", document: { id: "d1" } as Document }], state);
    expect(state.phase).toBe("done");
    expect(state.document?.id).toBe("d1");
  });

  it("ignores SCAN_STARTED outside ready", () => {
    expect(scanWizardReducer(initialScanState, { type: "SCAN_STARTED" }).phase).toBe("setup");
  });

  it("scan failure keeps pages and returns to ready with the error", () => {
    let state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "SCAN_STARTED" },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "SCAN_STARTED" },
      { type: "SCAN_FAILED", code: "scanner_jam", message: "jam" },
    ]);
    expect(state.phase).toBe("ready");
    expect(state.pages).toHaveLength(1);
    expect(state.error?.code).toBe("scanner_jam");
    state = scanWizardReducer(state, { type: "DISMISS_ERROR" });
    expect(state.error).toBeNull();
  });

  it("PAGE_DELETED renumbers the remaining pages", () => {
    const withPages = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(10, 1) },
      { type: "PAGE_SCANNED", page: page(11, 2) },
      { type: "PAGE_SCANNED", page: page(12, 3) },
    ]);
    const state = scanWizardReducer(withPages, { type: "PAGE_DELETED", pageId: 11 });
    expect(state.pages).toEqual([page(10, 1), page(12, 2)]);
  });

  it("PAGES_REORDERED replaces the list; COMPILE_FAILED returns to ready", () => {
    let state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "PAGE_SCANNED", page: page(2, 2) },
      { type: "PAGES_REORDERED", pages: [page(2, 1), page(1, 2)] },
    ]);
    expect(state.pages[0].id).toBe(2);
    state = reduceAll([{ type: "COMPILE_STARTED" }, { type: "COMPILE_FAILED", code: "no_pages", message: "x" }], state);
    expect(state.phase).toBe("ready");
    expect(state.error?.code).toBe("no_pages");
  });

  it("RESET returns to the initial state", () => {
    const state = reduceAll([{ type: "SESSION_STARTED", sessionId: 1 }, { type: "RESET" }]);
    expect(state).toEqual(initialScanState);
  });
});

describe("scannerMessage", () => {
  it("maps known codes and falls back otherwise", () => {
    expect(scannerMessage("scanner_offline", "x")).toMatch(/power|USB/i);
    expect(scannerMessage("weird_code", "fallback text")).toBe("fallback text");
  });
});
