import { describe, expect, it } from "vitest";
import {
  AGENT_INSTALLED_KEY,
  CLIENT_ID_KEY,
  detectPlatform,
  downloadUrl,
  getClientId,
  groupDevices,
  initialSearch,
  isAgentInstalled,
  markAgentInstalled,
  searchTick,
  startSearch,
} from "./localScan";

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
    clear: () => data.clear(),
    key: (i) => [...data.keys()][i] ?? null,
    get length() {
      return data.size;
    },
  };
}

const throwing = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
} as unknown as Storage;

describe("client id and install flag", () => {
  it("creates the client id once and keeps it", () => {
    const s = memoryStorage();
    const id = getClientId(s);
    expect(id).toMatch(/^[A-Za-z0-9-]{8,64}$/);
    expect(getClientId(s)).toBe(id);
    expect(s.getItem(CLIENT_ID_KEY)).toBe(id);
  });

  it("works when storage throws", () => {
    const id = getClientId(throwing);
    expect(id).toMatch(/^[A-Za-z0-9-]{8,64}$/);
    expect(getClientId(throwing)).toBe(id); // same id for the page lifetime
    expect(isAgentInstalled(throwing)).toBe(false);
    expect(() => markAgentInstalled(throwing)).not.toThrow();
  });

  it("stores the install flag", () => {
    const s = memoryStorage();
    expect(isAgentInstalled(s)).toBe(false);
    markAgentInstalled(s);
    expect(isAgentInstalled(s)).toBe(true);
    expect(s.getItem(AGENT_INSTALLED_KEY)).toBe("1");
  });
});

describe("search state machine", () => {
  it("asks to install when the flag is missing", () => {
    expect(startSearch(false, 0).phase).toBe("install");
  });

  it("finds scanners", () => {
    const s = startSearch(true, 0);
    expect(s.phase).toBe("searching");
    expect(searchTick(s, { now: 2000, agentConnected: true, localCount: 0 }).phase).toBe("searching");
    const live = searchTick(s, { now: 4000, agentConnected: true, localCount: 2 });
    expect(live).toMatchObject({ phase: "searching", found: 2 });
    expect(searchTick(live, { now: 14999, agentConnected: true, localCount: 3 })).toMatchObject({
      phase: "searching",
      found: 3,
    });
    const done = searchTick(live, { now: 15000, agentConnected: true, localCount: 3 });
    expect(done).toMatchObject({ phase: "found", found: 3 });
  });

  it("keeps the same state object when nothing changed", () => {
    const s = startSearch(true, 0);
    expect(searchTick(s, { now: 1000, agentConnected: true, localCount: 0 })).toBe(s);
  });

  it("reports none after the window when the agent is connected", () => {
    const s = startSearch(true, 0);
    expect(searchTick(s, { now: 14999, agentConnected: true, localCount: 0 }).phase).toBe("searching");
    expect(searchTick(s, { now: 15000, agentConnected: true, localCount: 0 }).phase).toBe("none");
  });

  it("reports an unresponsive agent after 10 s", () => {
    const s = startSearch(true, 0);
    expect(searchTick(s, { now: 9999, agentConnected: false, localCount: 0 }).phase).toBe("searching");
    expect(searchTick(s, { now: 10000, agentConnected: false, localCount: 0 }).phase).toBe("unresponsive");
  });

  it("ignores ticks when not searching", () => {
    expect(searchTick(initialSearch, { now: 99999, agentConnected: false, localCount: 3 })).toBe(initialSearch);
  });
});

describe("devices and platforms", () => {
  it("groups server and local devices", () => {
    const g = groupDevices([
      { id: "airscan:e0:HP", name: "HP" },
      { id: "agent:abcdefgh:u1", name: "Brother" },
    ]);
    expect(g.server.map((d) => d.name)).toEqual(["HP"]);
    expect(g.local.map((d) => d.name)).toEqual(["Brother"]);
  });

  it("detects the platform from the user agent", () => {
    expect(detectPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows-amd64");
    expect(detectPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)")).toBe("darwin-arm64");
    expect(detectPlatform("Mozilla/5.0 (X11; Linux x86_64)")).toBe("linux-amd64");
    expect(detectPlatform("Mozilla/5.0 (X11; Linux aarch64)")).toBe("linux-arm64");
    expect(detectPlatform("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)")).toBeNull();
    expect(detectPlatform("Mozilla/5.0 (Linux; Android 14)")).toBeNull();
  });

  it("builds the download url", () => {
    expect(downloadUrl("linux-amd64", "t k")).toBe("/api/agent/download/linux-amd64?token=t%20k");
    expect(downloadUrl("linux-amd64", null)).toBe("/api/agent/download/linux-amd64");
  });
});
