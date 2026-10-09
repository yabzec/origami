import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { useSelection } from "./useSelection";

it("selects, ranges, clears on view change and drops ids that left the list", () => {
  const { result, rerender } = renderHook(({ order, key }) => useSelection(order, key), {
    initialProps: { order: ["a", "b", "c"], key: "v1" },
  });
  act(() => result.current.toggle("a", false));
  act(() => result.current.toggle("c", true));
  expect([...result.current.selected].sort()).toEqual(["a", "b", "c"]);

  rerender({ order: ["a", "c"], key: "v1" }); // "b" was deleted
  expect([...result.current.selected].sort()).toEqual(["a", "c"]);

  rerender({ order: ["a", "c"], key: "v2" }); // folder or filter changed
  expect(result.current.selected.size).toBe(0);

  act(() => result.current.selectAll());
  expect(result.current.selected.size).toBe(2);
  act(() => result.current.clear());
  expect(result.current.selected.size).toBe(0);
});
