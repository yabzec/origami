import { afterEach, describe, expect, it, vi } from "vitest";
import { openAgentUrl } from "@/lib/agentLaunch";
import { guardBeforeUnload } from "./useLeaveGuard";

function unloadEvent() {
  const event = new Event("beforeunload", { cancelable: true }) as BeforeUnloadEvent;
  return Object.assign(event, { preventDefault: vi.fn() });
}

describe("guardBeforeUnload", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks before leaving", () => {
    const event = unloadEvent();
    guardBeforeUnload(event);
    expect(event.preventDefault).toHaveBeenCalled();
  });

  it("does not ask while an origami-agent link is being opened", () => {
    vi.useFakeTimers();
    const assign = vi.fn();
    vi.stubGlobal("location", { set href(url: string) { assign(url); } });
    try {
      openAgentUrl("origami-agent://connect?server=x&token=y");
    } finally {
      vi.unstubAllGlobals();
    }
    expect(assign).toHaveBeenCalledWith("origami-agent://connect?server=x&token=y");
    const during = unloadEvent();
    guardBeforeUnload(during);
    expect(during.preventDefault).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1000);
    const after = unloadEvent();
    guardBeforeUnload(after);
    expect(after.preventDefault).toHaveBeenCalled();
  });
});
