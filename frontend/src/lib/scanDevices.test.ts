import { describe, expect, it } from "vitest";
import { scanDeviceHint } from "./scanDevices";

describe("scanDeviceHint", () => {
  it("classifies device count", () => {
    expect(scanDeviceHint([])).toBe("none");
    expect(scanDeviceHint([{ id: "a", name: "A" }])).toBe("single");
    expect(scanDeviceHint([{ id: "a", name: "A" }, { id: "b", name: "B" }])).toBe("multiple");
  });
});
