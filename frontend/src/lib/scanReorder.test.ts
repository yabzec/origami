import { expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { applyReorder } from "./scanReorder";

const previous = [
  { id: 1, page_number: 1 },
  { id: 2, page_number: 2 },
];
const next = [
  { id: 2, page_number: 1 },
  { id: 1, page_number: 2 },
];

it("applies the new order first, then saves it", async () => {
  const dispatch = vi.fn();
  const post = vi.fn().mockResolvedValue({});
  await applyReorder({ sessionId: 7, previous, next, dispatch, post });
  expect(dispatch).toHaveBeenCalledTimes(1);
  expect(dispatch).toHaveBeenCalledWith({ type: "PAGES_REORDERED", pages: next });
  expect(post).toHaveBeenCalledWith("/api/scan/sessions/7/reorder", { page_ids: [2, 1] });
});

it("rolls back and reports the error when saving fails", async () => {
  const dispatch = vi.fn();
  const post = vi.fn().mockRejectedValue(new ApiError(422, "invalid_order", "bad order"));
  await applyReorder({ sessionId: 7, previous, next, dispatch, post });
  expect(dispatch.mock.calls.map((c) => c[0])).toEqual([
    { type: "PAGES_REORDERED", pages: next },
    { type: "PAGES_REORDERED", pages: previous },
    { type: "SCAN_FAILED", code: "invalid_order", message: "bad order" },
  ]);
});
