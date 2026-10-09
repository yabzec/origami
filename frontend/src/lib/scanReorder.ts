import { api, ApiError } from "./api";
import type { ScanAction } from "./scanWizard";
import type { ScanPageInfo } from "./types";

/** Optimistic reorder: show the new order at once, roll back if the server rejects it. */
export async function applyReorder({
  sessionId,
  previous,
  next,
  dispatch,
  post = api.post,
  isCurrent,
}: {
  sessionId: number;
  previous: ScanPageInfo[];
  next: ScanPageInfo[];
  dispatch: (action: ScanAction) => void;
  post?: (path: string, body: unknown) => Promise<unknown>;
  /** False once the session was discarded or replaced; a late failure is then ignored. */
  isCurrent: () => boolean;
}): Promise<void> {
  dispatch({ type: "PAGES_REORDERED", pages: next });
  try {
    await post(`/api/scan/sessions/${sessionId}/reorder`, { page_ids: next.map((p) => p.id) });
  } catch (err) {
    if (!isCurrent()) return;
    dispatch({ type: "PAGES_REORDERED", pages: previous });
    dispatch({
      type: "SCAN_FAILED",
      code: err instanceof ApiError ? err.code : "unknown",
      message: err instanceof ApiError ? err.message : "Could not reorder pages",
    });
  }
}
