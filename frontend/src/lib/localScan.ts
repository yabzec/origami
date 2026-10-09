import type { ScanDevice } from "./types";

export const CLIENT_ID_KEY = "origami.clientId";
export const AGENT_INSTALLED_KEY = "origami.agentInstalled";
export const SEARCH_WINDOW_MS = 15_000;
export const AGENT_WAIT_MS = 10_000;

function defaultStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

let fallbackClientId: string | null = null;

function newClientId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Stable id for this browser; lives for the page when storage is unavailable. */
export function getClientId(storage: Storage | undefined = defaultStorage()): string {
  try {
    const saved = storage?.getItem(CLIENT_ID_KEY);
    if (saved) return saved;
    const id = fallbackClientId ?? newClientId();
    storage?.setItem(CLIENT_ID_KEY, id);
    fallbackClientId = id;
    return id;
  } catch {
    fallbackClientId ??= newClientId();
    return fallbackClientId;
  }
}

export function isAgentInstalled(storage: Storage | undefined = defaultStorage()): boolean {
  try {
    return storage?.getItem(AGENT_INSTALLED_KEY) === "1";
  } catch {
    return false;
  }
}

export function markAgentInstalled(storage: Storage | undefined = defaultStorage()): void {
  try {
    storage?.setItem(AGENT_INSTALLED_KEY, "1");
  } catch {
    // storage blocked: the install panel shows again next time
  }
}

export type SearchPhase = "idle" | "install" | "searching" | "found" | "none" | "unresponsive";

export interface SearchState {
  phase: SearchPhase;
  startedAt: number | null;
  found: number;
}

export const initialSearch: SearchState = { phase: "idle", startedAt: null, found: 0 };

export function startSearch(installed: boolean, now: number): SearchState {
  return installed ? { phase: "searching", startedAt: now, found: 0 } : { phase: "install", startedAt: null, found: 0 };
}

export function searchTick(
  state: SearchState,
  input: { now: number; agentConnected: boolean; localCount: number },
): SearchState {
  if (state.phase !== "searching" || state.startedAt === null) return state;
  const elapsed = input.now - state.startedAt;
  if (!input.agentConnected && elapsed >= AGENT_WAIT_MS) return { ...state, phase: "unresponsive" };
  // Keep polling the whole window so scanners that appear late still show up.
  if (input.agentConnected && elapsed >= SEARCH_WINDOW_MS) {
    return input.localCount > 0
      ? { ...state, phase: "found", found: input.localCount }
      : { ...state, phase: "none", found: 0 };
  }
  return state.found === input.localCount ? state : { ...state, found: input.localCount };
}

export function isLocalDevice(id: string): boolean {
  return id.startsWith("agent:");
}

export function groupDevices(devices: ScanDevice[]): { server: ScanDevice[]; local: ScanDevice[] } {
  return {
    server: devices.filter((d) => !isLocalDevice(d.id)),
    local: devices.filter((d) => isLocalDevice(d.id)),
  };
}

export type AgentPlatform = "windows-amd64" | "darwin-arm64" | "darwin-amd64" | "linux-amd64" | "linux-arm64";

export const AGENT_PLATFORMS: { id: AgentPlatform; label: string }[] = [
  { id: "windows-amd64", label: "Windows" },
  { id: "darwin-arm64", label: "macOS (Apple silicon)" },
  { id: "darwin-amd64", label: "macOS (Intel)" },
  { id: "linux-amd64", label: "Linux (x86-64)" },
  { id: "linux-arm64", label: "Linux (ARM64)" },
];

/** Best guess from the user agent. Macs default to Apple silicon: browsers report "Intel" on both. */
export function detectPlatform(userAgent: string): AgentPlatform | null {
  const ua = userAgent.toLowerCase();
  if (/iphone|ipad|android/.test(ua)) return null;
  if (ua.includes("windows")) return "windows-amd64";
  if (ua.includes("macintosh") || ua.includes("mac os x")) return "darwin-arm64";
  if (ua.includes("linux")) return /aarch64|arm64/.test(ua) ? "linux-arm64" : "linux-amd64";
  return null;
}

export function downloadUrl(platform: AgentPlatform, token: string | null): string {
  const base = `/api/agent/download/${platform}`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}
