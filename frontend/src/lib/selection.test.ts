import { describe, expect, it } from "vitest";
import { rangeSelect, shouldClearOnEscape, toggleId } from "./selection";

const ORDER = ["a", "b", "c", "d", "e"];

describe("selection", () => {
  it("toggles one id without mutating the input", () => {
    const empty = new Set<string>();
    const one = toggleId(empty, "a");
    expect([...one]).toEqual(["a"]);
    expect(empty.size).toBe(0);
    expect([...toggleId(one, "a")]).toEqual([]);
  });

  it("adds the range between anchor and id, both directions", () => {
    expect([...rangeSelect(new Set(["b"]), ORDER, "b", "d")].sort()).toEqual(["b", "c", "d"]);
    expect([...rangeSelect(new Set(), ORDER, "d", "b")].sort()).toEqual(["b", "c", "d"]);
  });

  it("falls back to a toggle without a usable anchor", () => {
    expect([...rangeSelect(new Set(), ORDER, null, "c")]).toEqual(["c"]);
    expect([...rangeSelect(new Set(), ORDER, "zz", "c")]).toEqual(["c"]);
  });
});

describe("shouldClearOnEscape", () => {
  it("is false while a dialog is open", () => {
    expect(shouldClearOnEscape()).toBe(true);
    const d = document.createElement("div");
    d.setAttribute("role", "dialog");
    document.body.appendChild(d);
    expect(shouldClearOnEscape()).toBe(false);
    d.remove();
  });
});
