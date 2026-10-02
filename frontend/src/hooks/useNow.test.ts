import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useNow } from "./useNow";

describe("useNow", () => {
  afterEach(() => vi.useRealTimers());

  it("ticks on the given interval", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T10:00:00Z"));
    const { result } = renderHook(() => useNow(1000));
    expect(result.current.toISOString()).toBe("2026-10-03T10:00:00.000Z");
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.toISOString()).toBe("2026-10-03T10:00:03.000Z");
  });
});
