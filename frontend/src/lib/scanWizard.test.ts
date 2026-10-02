import { describe, expect, it } from "vitest";
import {
  initialScanState,
  scannerMessage,
  scanWizardReducer,
  shouldBlockLeave,
  type ScanState,
} from "./scanWizard";
import type { Document } from "./types";

const page = (id: number, page_number: number) => ({ id, page_number });

function reduceAll(actions: Parameters<typeof scanWizardReducer>[1][], from = initialScanState): ScanState {
  return actions.reduce(scanWizardReducer, from);
}

describe("scanWizardReducer", () => {
  it("starts in 'starting' and walks the happy path", () => {
    expect(initialScanState.phase).toBe("starting");
    let state = scanWizardReducer(initialScanState, { type: "SESSION_STARTED", sessionId: 5 });
    expect(state.phase).toBe("ready");
    expect(state.sessionId).toBe(5);

    state = scanWizardReducer(state, { type: "SCAN_STARTED" });
    expect(state.phase).toBe("scanning");
    state = scanWizardReducer(state, { type: "PAGE_SCANNED", page: page(1, 1) });
    expect(state.phase).toBe("ready");
    expect(state.selectedPageId).toBe(1);

    state = reduceAll([{ type: "COMPILE_STARTED" }, { type: "COMPILED", document: { id: "d1" } as Document }], state);
    expect(state.phase).toBe("done");
    expect(state.document?.id).toBe("d1");
  });

  it("session start failure stays in starting with the error", () => {
    const state = scanWizardReducer(initialScanState, { type: "SESSION_FAILED", code: "x", message: "down" });
    expect(state.phase).toBe("starting");
    expect(state.error?.message).toBe("down");
    expect(scanWizardReducer(state, { type: "DISMISS_ERROR" }).error).toBeNull();
  });

  it("ignores SCAN_STARTED outside ready", () => {
    expect(scanWizardReducer(initialScanState, { type: "SCAN_STARTED" }).phase).toBe("starting");
  });

  it("each new page becomes the selection; SELECT_PAGE changes it", () => {
    let state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(10, 1) },
      { type: "PAGE_SCANNED", page: page(11, 2) },
    ]);
    expect(state.selectedPageId).toBe(11);
    state = scanWizardReducer(state, { type: "SELECT_PAGE", pageId: 10 });
    expect(state.selectedPageId).toBe(10);
  });

  it("scan failure keeps pages and returns to ready with the error", () => {
    const state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "SCAN_STARTED" },
      { type: "SCAN_FAILED", code: "scanner_jam", message: "jam" },
    ]);
    expect(state.phase).toBe("ready");
    expect(state.pages).toHaveLength(1);
    expect(state.error?.code).toBe("scanner_jam");
  });

  it("PAGE_DELETED renumbers and moves the selection to the last page when needed", () => {
    const withPages = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(10, 1) },
      { type: "PAGE_SCANNED", page: page(11, 2) },
      { type: "PAGE_SCANNED", page: page(12, 3) },
    ]);
    const deletedSelected = scanWizardReducer(withPages, { type: "PAGE_DELETED", pageId: 12 });
    expect(deletedSelected.pages).toEqual([page(10, 1), page(11, 2)]);
    expect(deletedSelected.selectedPageId).toBe(11);

    const selectedFirst = scanWizardReducer(withPages, { type: "SELECT_PAGE", pageId: 10 });
    const deletedOther = scanWizardReducer(selectedFirst, { type: "PAGE_DELETED", pageId: 11 });
    expect(deletedOther.selectedPageId).toBe(10);
    expect(deletedOther.pages).toEqual([page(10, 1), page(12, 2)]);

    const empty = reduceAll(
      [{ type: "PAGE_DELETED", pageId: 10 }, { type: "PAGE_DELETED", pageId: 11 }, { type: "PAGE_DELETED", pageId: 12 }],
      withPages,
    );
    expect(empty.selectedPageId).toBeNull();
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

  it("RESET returns to the initial state (a new session will start)", () => {
    const state = reduceAll([{ type: "SESSION_STARTED", sessionId: 1 }, { type: "RESET" }]);
    expect(state).toEqual(initialScanState);
  });
});

describe("shouldBlockLeave", () => {
  it("blocks only with unsaved pages", () => {
    const ready = scanWizardReducer(initialScanState, { type: "SESSION_STARTED", sessionId: 1 });
    expect(shouldBlockLeave(ready)).toBe(false);
    const withPage = scanWizardReducer(ready, { type: "PAGE_SCANNED", page: page(1, 1) });
    expect(shouldBlockLeave(withPage)).toBe(true);
    const done = scanWizardReducer(withPage, { type: "COMPILED", document: { id: "d" } as Document });
    expect(shouldBlockLeave(done)).toBe(false);
  });

  it("does not block while compiling (pages are being saved)", () => {
    const withPage = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "COMPILE_STARTED" },
    ]);
    expect(withPage.phase).toBe("compiling");
    expect(shouldBlockLeave(withPage)).toBe(false);
  });
});

describe("stale results without a session", () => {
  it("SCAN_FAILED with no session keeps phase starting and sets error", () => {
    const next = scanWizardReducer(initialScanState, { type: "SCAN_FAILED", code: "scanner_busy", message: "busy" });
    expect(next.phase).toBe("starting");
    expect(next.error).toEqual({ code: "scanner_busy", message: "busy" });
  });

  it("COMPILE_FAILED with no session keeps phase starting and sets error", () => {
    const next = scanWizardReducer(initialScanState, { type: "COMPILE_FAILED", code: "x", message: "y" });
    expect(next.phase).toBe("starting");
    expect(next.error).toEqual({ code: "x", message: "y" });
  });
});

describe("scannerMessage", () => {
  it("maps known codes and falls back otherwise", () => {
    expect(scannerMessage("scanner_offline", "x")).toMatch(/power|USB/i);
    expect(scannerMessage("weird_code", "fallback text")).toBe("fallback text");
  });
});
